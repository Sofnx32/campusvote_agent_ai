import Redis from 'ioredis';
import { config } from './config.js';
import { AgentError } from './errors.js';

const memory = new Map();
const pendingActions = new Map();
const ACTION_TTL_SECONDS = 600;
const now = () => Date.now();

const purge = (key) => {
  const entry = memory.get(key);
  if (entry && entry.expiresAt <= now()) memory.delete(key);
};

export class ReplayStore {
  constructor(redis = null, clock = now) {
    this.redis = redis;
    this.clock = clock;
  }

  async redisCall(operation) {
    try {
      return await operation();
    } catch (error) {
      throw new AgentError('REDIS_UNAVAILABLE', 'Replay state store is unavailable', 503, true, { cause: error });
    }
  }

  static async create() {
    if (config.redisUrl) return new ReplayStore(new Redis(config.redisUrl, { lazyConnect: false }));
    if (config.nodeEnv === 'production' || !config.allowInMemoryReplay) {
      throw new AgentError('AI_CONTEXT_REPLAY_STORE_UNAVAILABLE', 'Replay store is not configured', 503, true);
    }
    return new ReplayStore();
  }

  async setIssued(jti, value) {
    if (this.redis) {
      const result = await this.redisCall(() => this.redis.set(`ai:context-jti:${jti}`, JSON.stringify({ status: 'issued', request_open: true, correlation_id: value.correlationId, deadline_at: value.deadlineAt, user_id: value.userId }), 'NX', 'EX', config.jtiTtlSeconds));
      if (result !== 'OK') throw new AgentError('AI_CONTEXT_REPLAYED', 'Context jti already exists', 409);
      return;
    }
    purge(jti);
    if (memory.has(jti)) throw new AgentError('AI_CONTEXT_REPLAYED', 'Context jti already exists', 409);
    memory.set(jti, { status: 'issued', request_open: true, ...value, expiresAt: now() + config.jtiTtlSeconds * 1000 });
  }

  async consume(jti, expected) {
    if (this.redis) {
      const script = `
        local value = redis.call('GET', KEYS[1])
        if not value then return 'missing' end
        local decoded = cjson.decode(value)
        if decoded.status ~= 'issued' then return 'consumed' end
        if decoded.correlation_id ~= ARGV[1] then return 'mismatch' end
        decoded.status = 'consumed'
        redis.call('SET', KEYS[1], cjson.encode(decoded), 'KEEPTTL')
        return 'ok'
      `;
      const result = await this.redisCall(() => this.redis.eval(script, 1, `ai:context-jti:${jti}`, expected.correlationId));
      if (result !== 'ok') throw new AgentError(result === 'mismatch' ? 'AI_CONTEXT_INVALID' : 'AI_CONTEXT_REPLAYED', 'Context cannot be consumed', result === 'mismatch' ? 401 : 409);
      return;
    }
    purge(jti);
    const entry = memory.get(jti);
    if (!entry || entry.status !== 'issued') throw new AgentError('AI_CONTEXT_REPLAYED', 'Context cannot be consumed', 409);
    if (entry.correlationId !== expected.correlationId) throw new AgentError('AI_CONTEXT_INVALID', 'Context correlation mismatch', 401);
    entry.status = 'consumed';
  }

  async reserveTool(jti, toolCallId, maxCalls) {
    const key = `ai:tool-call:${jti}:${toolCallId}`;
    if (this.redis) {
      const state = await this.redisCall(() => this.redis.get(`ai:context-jti:${jti}`));
      if (!state || JSON.parse(state).status !== 'consumed' || JSON.parse(state).request_open !== true) throw new AgentError('AI_CONTEXT_REPLAYED', 'Context is not active', 409);
      const count = await this.redisCall(() => this.redis.incr(`ai:tool-count:${jti}`));
      if (count === 1) await this.redisCall(() => this.redis.expire(`ai:tool-count:${jti}`, config.jtiTtlSeconds));
      if (count > maxCalls) throw new AgentError('AI_RATE_LIMITED', 'Tool call budget exceeded', 429, true);
      const result = await this.redisCall(() => this.redis.set(key, '1', 'NX', 'EX', config.jtiTtlSeconds));
      if (result !== 'OK') throw new AgentError('AI_CONTEXT_REPLAYED', 'Tool call already used', 409);
      return;
    }
    purge(jti);
    const entry = memory.get(jti);
    if (!entry || entry.status !== 'consumed' || entry.request_open !== true) throw new AgentError('AI_CONTEXT_REPLAYED', 'Context is not active', 409);
    entry.toolCalls ||= new Set();
    if (entry.toolCalls.size >= maxCalls) throw new AgentError('AI_RATE_LIMITED', 'Tool call budget exceeded', 429, true);
    if (entry.toolCalls.has(toolCallId)) throw new AgentError('AI_CONTEXT_REPLAYED', 'Tool call already used', 409);
    entry.toolCalls.add(toolCallId);
  }

  async closeRequest(jti) {
    if (this.redis) {
      const script = `
        local value = redis.call('GET', KEYS[1])
        if not value then return 0 end
        local decoded = cjson.decode(value)
        decoded.request_open = false
        redis.call('SET', KEYS[1], cjson.encode(decoded), 'KEEPTTL')
        return 1
      `;
      await this.redisCall(() => this.redis.eval(script, 1, `ai:context-jti:${jti}`));
      return;
    }
    const entry = memory.get(jti);
    if (entry) entry.request_open = false;
  }

  async setPendingAction(key, value) {
    const storageKey = `ai:pending-action:${key}`;
    if (this.redis) {
      await this.redisCall(() => this.redis.set(storageKey, JSON.stringify(value), 'EX', ACTION_TTL_SECONDS));
      return;
    }
    pendingActions.set(key, { value, expiresAt: this.clock() + ACTION_TTL_SECONDS * 1000 });
  }

  async getPendingAction(key) {
    if (this.redis) {
      const value = await this.redisCall(() => this.redis.get(`ai:pending-action:${key}`));
      return value ? JSON.parse(value) : null;
    }
    const entry = pendingActions.get(key);
    if (!entry || entry.expiresAt <= this.clock()) {
      pendingActions.delete(key);
      return null;
    }
    return entry.value;
  }

  async consumePendingAction(key, expectedDigest) {
    if (this.redis) {
      const script = `
        local value = redis.call('GET', KEYS[1])
        if not value then return 'missing' end
        local decoded = cjson.decode(value)
        if decoded.digest ~= ARGV[1] then return 'mismatch' end
        redis.call('DEL', KEYS[1])
        return 'ok'
      `;
      return await this.redisCall(() => this.redis.eval(script, 1, `ai:pending-action:${key}`, expectedDigest)) === 'ok';
    }
    const entry = await this.getPendingAction(key);
    if (!entry || entry.digest !== expectedDigest) return false;
    pendingActions.delete(key);
    return true;
  }

  async clearPendingAction(key) {
    if (this.redis) {
      await this.redisCall(() => this.redis.del(`ai:pending-action:${key}`));
      return;
    }
    pendingActions.delete(key);
  }
}
