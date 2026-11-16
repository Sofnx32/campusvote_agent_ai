import express from 'express';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { Agent as UndiciAgent } from 'undici';
import { config, assertProductionConfiguration, httpsDispatcher } from './config.js';
import { AgentError, errorResponse, publicError } from './errors.js';
import { jsonParserErrorHandler } from './utils/http-middleware.js';
import { extractBearer, verifyUser, validateActor, signContext, verifyContext } from './utils/context.js';
import { ReplayStore } from './state/replay-store.js';
import { BackendAdapter } from './services/backend-adapter.js';
import { fairActionScopeKey, operationDigest, isExplicitAffirmation, isFairCancellation, isFairConfirmation, isFairResume, preserveUploadedFairImage, validateWriteProposal } from './validators/fair-action.js';
import { buildOperationResult } from './views/result-view.js';
import { registerHealthRoutes } from './routes/health.js';
import { registerChatRoutes } from './routes/chat.js';
import { registerToolsRoutes } from './routes/tools.js';

assertProductionConfiguration();

const app = express();
app.use((req, res, next) => {
  const origin = req.get('origin');
  if (origin && config.allowedOrigins.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Correlation-Id, X-Request-Id');
    res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
  }
  if (req.method === 'OPTIONS') return res.sendStatus(origin && config.allowedOrigins.includes(origin) ? 204 : 403);
  return next();
});

const replay = await ReplayStore.create();
const backend = new BackendAdapter();
const userTokens = new Map();

app.use((req, res, next) => {
  const incoming = req.get('X-Correlation-Id') || req.get('X-Request-Id');
  const requestId = typeof incoming === 'string' && /^[a-zA-Z0-9._:-]{1,128}$/.test(incoming)
    ? incoming : crypto.randomUUID();
  req.requestId = requestId;
  req.startedAt = Date.now();
  res.setHeader('X-Request-Id', requestId);
  res.setHeader('X-Correlation-Id', requestId);
  res.on('finish', () => console.info(JSON.stringify({
    event: 'request_complete', service: 'agent-node', requestId,
    method: req.method, route: req.route?.path || req.path,
    status: res.statusCode, durationMs: Date.now() - req.startedAt,
  })));
  next();
});
app.use(express.json({ limit: '64kb' }));
app.use(jsonParserErrorHandler);

// Rutas
registerHealthRoutes(app);
registerChatRoutes(app, replay, backend);
registerToolsRoutes(app, replay, backend, userTokens);

// 404
app.use((req, res) => {
  const correlationId = req.requestId || crypto.randomUUID();
  const error = new AgentError('AI_RESOURCE_NOT_FOUND', 'No se encontró el recurso solicitado.', 404);
  res.status(404).json(errorResponse(error, correlationId));
});

// Error handler
app.use((error, req, res, _next) => {
  const correlationId = req.requestId || crypto.randomUUID();
  const badJson = error instanceof SyntaxError && Object.hasOwn(error, 'body');
  const normalized = publicError(badJson
    ? new AgentError('VALIDATION_INVALID_JSON', 'Revisa la información ingresada.', 400)
    : error);
  console.error(JSON.stringify({
    event: 'request_failed', service: 'agent-node', requestId: correlationId,
    method: req?.method, route: req?.route?.path || req?.path,
    status: normalized.status, code: normalized.code,
    cause: error?.name || 'Error', durationMs: req.startedAt ? Date.now() - req.startedAt : undefined,
  }));
  if (!res.headersSent) res.status(normalized.status).json(errorResponse(normalized, correlationId));
});

app.listen(config.port, () => console.log(JSON.stringify({ event: 'agent_node_started', port: config.port })));
export { app };