import crypto from 'node:crypto';
import { config } from '../config.js';
import { AgentError } from '../errors.js';

const SESSION_PREFIX = 'ai:admin-session:';
const SESSION_BY_USER_PREFIX = 'ai:admin-session-by-user:';

function getConfigValue(key, defaultValue) {
  // Allow environment variables to override frozen config for testing
  const envValue = process.env[key];
  if (envValue !== undefined) {
    if (key.endsWith('SECONDS') || key.endsWith('_MS')) {
      return Number(envValue);
    }
    return envValue;
  }
  return config[key] ?? defaultValue;
}

export class AdminSessionStore {
  constructor(redis = null, clock = Date.now, options = {}) {
    this.redis = redis;
    this._clock = clock;
    this._memory = null;
    this._nodeEnv = options.nodeEnv ?? process.env.NODE_ENV ?? 'development';
    this._allowInMemoryReplay = options.allowInMemoryReplay ?? (process.env.ALLOW_IN_MEMORY_REPLAY === 'true');
    this._sessionTtl = options.sessionTtlSeconds ?? Number(process.env.AI_ADMIN_SESSION_TTL_SECONDS ?? 28800);
    this._sessionIdleTtl = options.sessionIdleTtlSeconds ?? Number(process.env.AI_ADMIN_SESSION_IDLE_TTL_SECONDS ?? 7200);
  }

  static async create(options = {}) {
    const redisUrl = options.redisUrl ?? process.env.REDIS_URL ?? '';
    const nodeEnv = options.nodeEnv ?? process.env.NODE_ENV ?? 'development';
    const allowInMemoryReplay = options.allowInMemoryReplay ?? (process.env.ALLOW_IN_MEMORY_REPLAY === 'true');
    const clock = options.clock ?? Date.now;

    if (redisUrl) {
      const Redis = (await import('ioredis')).default;
      return new AdminSessionStore(new Redis(redisUrl, { lazyConnect: false }), clock, options);
    }
    if (nodeEnv === 'production' || !allowInMemoryReplay) {
      throw new AgentError('AI_CONTEXT_REPLAY_STORE_UNAVAILABLE', 'Admin session store is not configured', 503, true);
    }
    return new AdminSessionStore(null, clock, options);
  }

  _sessionKey(sessionId) {
    return `${SESSION_PREFIX}${sessionId}`;
  }

  _userKey(userId) {
    return `${SESSION_BY_USER_PREFIX}${userId}`;
  }

  _sessionTtlSeconds() {
    return this._sessionTtl;
  }

  _sessionIdleTtlSeconds() {
    return this._sessionIdleTtl;
  }

  async createSession(userId, organizationId, siteId, siteName, timezone, userToken, correlationId) {
    const sessionId = crypto.randomUUID();
    const now = this._clock();
    const expiresAt = now + this._sessionTtlSeconds() * 1000;
    const idleExpiresAt = now + this._sessionIdleTtlSeconds() * 1000;

    const session = {
      sessionId,
      userId,
      organizationId,
      siteId,
      siteName,
      timezone,
      userToken, // CampusVote user JWT for backend calls
      status: 'active',
      createdAt: new Date(now).toISOString(),
      lastActivityAt: new Date(now).toISOString(),
      expiresAt: new Date(expiresAt).toISOString(),
      idleExpiresAt: new Date(idleExpiresAt).toISOString(),
      correlationId, // Initial correlation ID for tracing
    };

    if (this.redis) {
      const pipeline = this.redis.pipeline();
      pipeline.set(this._sessionKey(sessionId), JSON.stringify(session), 'EX', this._sessionTtlSeconds());
      pipeline.set(this._userKey(userId), sessionId, 'EX', this._sessionTtlSeconds());
      await pipeline.exec();
    } else {
      // In-memory fallback (single instance only)
      if (!this._memory) this._memory = new Map();
      this._memory.set(this._sessionKey(sessionId), { value: session, expiresAt });
      this._memory.set(this._userKey(userId), { value: sessionId, expiresAt });
    }

    return session;
  }

  async getSession(sessionId) {
    let raw;
    if (this.redis) {
      raw = await this.redis.get(this._sessionKey(sessionId));
    } else {
      this._purgeMemory();
      const entry = this._memory?.get(this._sessionKey(sessionId));
      raw = entry?.value ? JSON.stringify(entry.value) : null;
    }
    if (!raw) return null;
    const session = JSON.parse(raw);
    // Check expiration using clock for testability
    const now = this._clock();
    if (new Date(session.expiresAt).getTime() <= now) {
      await this.deleteSession(sessionId);
      return null;
    }
    if (new Date(session.idleExpiresAt).getTime() <= now) {
      await this.deleteSession(sessionId);
      return null;
    }
    if (session.status !== 'active') {
      return null;
    }
    return session;
  }

  async getSessionByUserId(userId) {
    let sessionId;
    if (this.redis) {
      sessionId = await this.redis.get(this._userKey(userId));
    } else {
      this._purgeMemory();
      const entry = this._memory?.get(this._userKey(userId));
      sessionId = entry?.value;
    }
    if (!sessionId) return null;
    return this.getSession(sessionId);
  }

  async updateActivity(sessionId) {
    const session = await this.getSession(sessionId);
    if (!session) return null;
    const now = this._clock();
    const idleExpiresAt = now + this._sessionIdleTtlSeconds() * 1000;
    session.lastActivityAt = new Date(now).toISOString();
    session.idleExpiresAt = new Date(idleExpiresAt).toISOString();
    return this._saveSession(session);
  }

  async revokeSession(sessionId) {
    const session = await this.getSession(sessionId);
    if (!session) return false;
    session.status = 'revoked';
    session.revokedAt = new Date().toISOString();
    await this._saveSession(session);
    // Also remove user mapping
    if (this.redis) {
      await this.redis.del(this._userKey(session.userId));
    } else {
      this._memory?.delete(this._userKey(session.userId));
    }
    return true;
  }

  async revokeSessionByUserId(userId) {
    const session = await this.getSessionByUserId(userId);
    if (!session) return false;
    return this.revokeSession(session.sessionId);
  }

  async deleteSession(sessionId) {
    let session;
    if (this.redis) {
      const raw = await this.redis.get(this._sessionKey(sessionId));
      if (raw) {
        session = JSON.parse(raw);
        await this.redis.del(this._sessionKey(sessionId));
        if (session?.userId) {
          await this.redis.del(this._userKey(session.userId));
        }
      }
    } else {
      this._purgeMemory();
      const entry = this._memory?.get(this._sessionKey(sessionId));
      if (entry?.value?.userId) {
        this._memory?.delete(this._userKey(entry.value.userId));
      }
      this._memory?.delete(this._sessionKey(sessionId));
    }
    return true;
  }

  _purgeMemory() {
    if (!this._memory) return;
    const now = this._clock();
    for (const [key, entry] of this._memory.entries()) {
      if (entry.expiresAt <= now) {
        this._memory.delete(key);
      }
    }
  }

  async _saveSession(session) {
    const ttlSeconds = Math.ceil((new Date(session.expiresAt).getTime() - this._clock()) / 1000);
    if (ttlSeconds <= 0) {
      await this.deleteSession(session.sessionId);
      return null;
    }
    if (this.redis) {
      await this.redis.set(this._sessionKey(session.sessionId), JSON.stringify(session), 'EX', ttlSeconds);
      // Also update user mapping TTL
      await this.redis.expire(this._userKey(session.userId), ttlSeconds);
    } else {
      if (!this._memory) this._memory = new Map();
      this._memory.set(this._sessionKey(session.sessionId), { value: session, expiresAt: new Date(session.expiresAt).getTime() });
      this._memory.set(this._userKey(session.userId), { value: session.sessionId, expiresAt: new Date(session.expiresAt).getTime() });
    }
    return session;
  }
}

// Re-export for convenience
export const AdminSessionError = {
  SESSION_EXPIRED: 'SESSION_EXPIRED',
  SESSION_REVOKED: 'SESSION_REVOKED',
  SESSION_NOT_FOUND: 'SESSION_NOT_FOUND',
  SESSION_INVALID: 'SESSION_INVALID',
};