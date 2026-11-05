import express from 'express';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { Agent as UndiciAgent } from 'undici';
import { config, assertProductionConfiguration } from './config.js';
import { AgentError, errorResponse } from './errors.js';
import { extractBearer, verifyUser, validateActor, signContext, verifyContext } from './context.js';
import { ReplayStore } from './replay-store.js';
import { BackendAdapter } from './backend-adapter.js';

assertProductionConfiguration();
const app = express();
app.use((req, res, next) => {
  const origin = req.get('origin');
  if (origin && config.allowedOrigins.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Correlation-Id');
    res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
  }
  if (req.method === 'OPTIONS') return res.sendStatus(origin && config.allowedOrigins.includes(origin) ? 204 : 403);
  return next();
});
app.use(express.json({ limit: '64kb' }));
const replay = await ReplayStore.create();
const backend = new BackendAdapter();
const activeRequests = new Set();
const userTokens = new Map();
const tools = new Set([
  'getSiteOverview',
  'getSiteFairs',
  'getSiteProjects',
  'getSiteStands',
  'getSiteResults',
  'createFairRubric',
  'addFairRubricCriterion',
  'createFair',
  'updateFair',
  'changeFairStatus',
  'listFairStands',
  'createFairStand',
  'updateFairStand',
  'deleteFairStand',
]);
const httpsDispatcher = config.mtlsRequired ? new UndiciAgent({
  connect: {
    ca: fs.readFileSync(config.mtlsCaPath),
    cert: fs.readFileSync(config.mtlsCertPath),
    key: fs.readFileSync(config.mtlsKeyPath),
    rejectUnauthorized: true,
  },
}) : undefined;

const withTimeout = (ms, task) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return Promise.resolve().then(() => task(controller.signal)).finally(() => clearTimeout(timer));
};

const requireServiceToken = (req) => {
  if (req.get('authorization') !== `Bearer ${config.serviceToken}`) throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'Invalid service token', 401);
};

const handleError = (error, correlationId, res) => {
  const normalized = error.name === 'AbortError' ? new AgentError('AI_TOOL_TIMEOUT', 'Operation timed out', 504, true) : error;
  res.status(normalized.status || 500).json(errorResponse(normalized, correlationId));
};

app.get('/health', (_req, res) => res.json({ success: true, service: 'campusvote-agent-node' }));

app.post('/v1/chat', async (req, res) => {
  const correlationId = req.get('X-Correlation-Id') || req.body?.correlation_id || crypto.randomUUID();
  try {
    const userToken = extractBearer(req.get('authorization'));
    const userClaims = verifyUser(userToken);
    const correlation = req.body?.correlation_id;
    if (correlation && correlation !== correlationId) throw new AgentError('AI_INVALID_REQUEST', 'Correlation id mismatch', 400);
    if (typeof req.body?.message !== 'string' || req.body.message.length < 1 || req.body.message.length > 4000) throw new AgentError('AI_INVALID_REQUEST', 'Message must contain 1-4000 characters', 400);
    const resolvedActor = await withTimeout(
      config.toolTimeoutMs,
      async (signal) => backend.resolveActorContext(userToken, signal)
    );
    const actor = validateActor(resolvedActor);
    const tokenUserId = userClaims.userId ?? userClaims.sub;
    if (!tokenUserId || tokenUserId !== actor.userId) {
      throw new AgentError('AI_CONTEXT_INVALID', 'User token does not match the resolved administrator', 401);
    }
    const deadlineAt = new Date(Date.now() + config.chatTimeoutMs).toISOString();
    const context = signContext(actor, correlationId, deadlineAt);
    await replay.setIssued(context.jti, { correlationId, deadlineAt, userId: actor.userId });
    await replay.consume(context.jti, { correlationId });
    userTokens.set(context.jti, userToken);
    activeRequests.add(context.jti);
    try {
      const response = await withTimeout(config.chatTimeoutMs, async (signal) => {
        const upstream = await fetch(new URL('/v1/chat', config.pythonUrl), {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            authorization: `Bearer ${config.serviceToken}`,
            'X-AI-Context': context.token,
            'X-Correlation-Id': correlationId,
          },
          body: JSON.stringify({ message: req.body.message, conversation_id: req.body.conversation_id || null, correlation_id: correlationId }),
          dispatcher: httpsDispatcher,
          signal,
        });
        const payload = await upstream.json();
        if (!upstream.ok) throw new AgentError(payload.error?.code || 'AI_INTERNAL_ERROR', payload.error?.message || 'AI service failed', upstream.status, payload.error?.retryable);
        return payload;
      });
      return res.json(response);
    } finally {
      await replay.closeRequest(context.jti);
      userTokens.delete(context.jti);
      activeRequests.delete(context.jti);
    }
  } catch (error) {
    return handleError(error, correlationId, res);
  }
});

app.post('/internal/ai/v1/tools/:tool', async (req, res) => {
  const correlationId = req.get('X-Correlation-Id') || req.body?.correlation_id;
  try {
    requireServiceToken(req);
    if (!tools.has(req.params.tool)) throw new AgentError('AI_TOOL_NOT_FOUND', 'Tool is not allowlisted', 404);
    const context = verifyContext(req.get('X-AI-Context'));
    if (context.correlation_id !== correlationId || req.body?.correlation_id !== correlationId) throw new AgentError('AI_INVALID_REQUEST', 'Correlation id mismatch', 400);
    if (typeof req.body?.tool_call_id !== 'string' || !req.body.tool_call_id) throw new AgentError('AI_INVALID_REQUEST', 'tool_call_id is required', 400);
    const siteId = req.body?.input?.site_id;
    if (typeof siteId !== 'string' || siteId !== context.site_id) throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not authorized', 403);
    await replay.reserveTool(context.jti, req.body.tool_call_id, config.maxToolCalls);
    const userToken = userTokens.get(context.jti);
    if (!userToken) throw new AgentError('AI_CONTEXT_REPLAYED', 'Original authenticated request is no longer active', 409);
    const input = { ...(req.body?.input ?? {}), site_id: siteId };
    await withTimeout(config.toolTimeoutMs, async (signal) => backend.assertActiveAssignment(context, siteId, userToken, signal));
    const data = await withTimeout(config.toolTimeoutMs, async (signal) => backend.callTool(req.params.tool, input, context, userToken, signal));
    res.json({ success: true, data, meta: { tool: req.params.tool, correlation_id: correlationId, truncated: false, resource_refs: [] } });
  } catch (error) {
    handleError(error, correlationId, res);
  }
});

app.listen(config.port, () => console.log(JSON.stringify({ event: 'agent_node_started', port: config.port })));
export { app };
