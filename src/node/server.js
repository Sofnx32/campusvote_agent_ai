import express from 'express';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { Agent as UndiciAgent } from 'undici';
import { config, assertProductionConfiguration } from './config.js';
import { AgentError, errorResponse, publicError } from './errors.js';
import { readJsonResponse } from './http-response.js';
import { jsonParserErrorHandler } from './http-middleware.js';
import { extractBearer, verifyUser, validateActor, signContext, verifyContext } from './context.js';
import { ReplayStore } from './replay-store.js';
import { BackendAdapter } from './backend-adapter.js';
import { fairActionScopeKey, operationDigest, isFairCancellation, isFairConfirmation, isFairResume, preserveUploadedFairImage, validateWriteProposal } from './fair-action.js';

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
const activeRequests = new Set();
const userTokens = new Map();
const tools = new Set([
  'getSiteOverview',
  'getSiteFairs',
  'getSiteProjects',
  'getSiteStands',
  'getSiteResults',
  'listTeachingCriteria',
  'createTeachingCriterion',
  'updateTeachingCriterion',
  'toggleTeachingCriterion',
  'deleteTeachingCriterion',
  'listFairCategories',
  'listFairCategoryRubrics',
  'createFairRubric',
  'addFairRubricCriterion',
  'createCategoryFairRubric',
  'addCategoryFairRubricCriterion',
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

const operationTitles = {
  createFair: 'Crear feria',
  createTeachingCriterion: 'Crear criterio de evaluación docente',
  updateTeachingCriterion: 'Actualizar criterio de evaluación docente',
  toggleTeachingCriterion: 'Cambiar estado del criterio docente',
  deleteTeachingCriterion: 'Eliminar criterio de evaluación docente',
  createFairRubric: 'Crear rúbrica de feria',
  addFairRubricCriterion: 'Agregar criterio a la rúbrica',
  createCategoryFairRubric: 'Crear rúbrica por categoría',
  addCategoryFairRubricCriterion: 'Agregar criterio por categoría',
  updateFair: 'Actualizar feria',
  changeFairStatus: 'Cambiar estado de feria',
  createFairStand: 'Crear stand',
  updateFairStand: 'Actualizar stand',
  deleteFairStand: 'Eliminar stand',
};
const operationLabels = {
  name: 'Nombre', description: 'Descripción', image_url: 'Portada', starts_at: 'Inicio',
  ends_at: 'Fin', registration_deadline: 'Cierre de inscripción', min_score: 'Puntaje mínimo',
  max_score: 'Puntaje máximo', points: 'Puntos', position: 'Posición', code: 'Código del stand',
  status: 'Estado',
  career_id: 'Carrera',
};
const operationField = (name, value, tool) => ({
  name,
  type: name === 'image_url' ? 'image'
    : ['starts_at', 'ends_at', 'registration_deadline'].includes(name) ? 'date'
    : ['min_score', 'max_score', 'points', 'position'].includes(name) ? 'number'
      : name === 'description' ? 'textarea'
        : name === 'status' ? 'select' : 'text',
  label: operationLabels[name] || name.replaceAll('_', ' '),
  required: (tool === 'createFair' && ['name', 'image_url'].includes(name)) ||
    (tool === 'createCategoryFairRubric' && name === 'name') ||
    (tool === 'addCategoryFairRubricCriterion' && ['name', 'points'].includes(name)) ||
    (tool === 'createFairRubric' && name === 'name') ||
    (tool === 'addFairRubricCriterion' && ['name', 'min_score', 'max_score'].includes(name)) ||
    (tool === 'createFairStand' && name === 'code'),
  ...(name === 'description' ? { max_length: 5000 } : {}),
  ...(name === 'name' ? { max_length: tool === 'createFair' ? 200 : 255 } : {}),
  ...(name === 'status' ? { options: ['DRAFT', 'OPEN', 'CLOSED'].map((status) => ({ value: status, label: status })) } : {}),
  ...(name === 'image_url' && typeof value === 'string' ? {} : {}),
});

const pendingActionUi = (pendingAction, siteName) => {
  const proposal = pendingAction.proposal || {};
  const fields = pendingAction.tool === 'createFair' ? [
    { name: 'name', type: 'text', label: 'Nombre', required: true, max_length: 200 },
    { name: 'description', type: 'textarea', label: 'Descripción', required: false, max_length: 5000 },
    { name: 'starts_at', type: 'date', label: 'Fecha de inicio', required: false },
    { name: 'ends_at', type: 'date', label: 'Fecha de cierre', required: false },
    { name: 'registration_deadline', type: 'date', label: 'Límite de registro de proyectos', required: false },
    { name: 'image_url', type: 'image', label: 'Portada', required: true },
  ] : Object.entries(proposal)
    .filter(([key]) => !key.endsWith('_id') && key !== 'site_id')
    .map(([key, value]) => operationField(key, value, pendingAction.tool));
  return {
    type: 'confirmation',
    schema: { title: operationTitles[pendingAction.tool] || 'Revisar operación', fields },
    data: { ...Object.fromEntries(Object.entries(proposal).filter(([key]) => !key.endsWith('_id') && key !== 'site_id')), site_name: siteName },
    actions: ['confirm', 'edit', 'cancel'],
  };
};

const requireServiceToken = (req) => {
  if (req.get('authorization') !== `Bearer ${config.serviceToken}`) throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'Invalid service token', 401);
};

const handleError = (error, correlationId, res, req, startedAt) => {
  const normalized = publicError(error);
  console.error(JSON.stringify({
    event: 'request_failed', service: 'agent-node', requestId: correlationId,
    method: req?.method, route: req?.route?.path || req?.path,
    status: normalized.status, code: normalized.code,
    cause: error?.name || 'Error', durationMs: startedAt ? Date.now() - startedAt : undefined,
  }));
  if (!res.headersSent) res.status(normalized.status).json(errorResponse(normalized, correlationId));
};

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

app.get('/health', (_req, res) => res.json({ success: true, service: 'campusvote-agent-node' }));

// Readiness probe used by the authenticated frontend session. Calling this
// endpoint also sends inbound traffic to Python so a free Render instance can
// wake before the user submits a chat message.
app.get('/health/agent', async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  try {
    const upstream = await withTimeout(5000, (signal) => fetch(new URL('/health', config.pythonUrl), {
      signal,
      dispatcher: httpsDispatcher,
      headers: { 'X-Correlation-Id': req.requestId },
    }));
    if (!upstream.ok) throw new AgentError('AI_SERVICE_UNAVAILABLE', 'Python readiness check failed', 503, true);
    const payload = await readJsonResponse(upstream, 'python');
    if (payload?.success !== true || payload?.service !== 'campusvote-agent-python') {
      throw new AgentError('AI_SERVICE_UNAVAILABLE', 'Python readiness response is invalid', 503, true);
    }
    return res.json({ success: true, ready: true, service: 'campusvote-agent-node' });
  } catch (error) {
    console.warn(JSON.stringify({
      event: 'dependency_not_ready', service: 'agent-node', dependency: 'agent-python',
      requestId: req.requestId, cause: error?.name || 'Error',
      status: error?.status || undefined,
    }));
    return res.status(503).json({
      success: false, ready: false,
      error: { code: 'AI_SERVICE_UNAVAILABLE', message: 'El asistente se está iniciando.', requestId: req.requestId },
      requestId: req.requestId,
    });
  }
});

app.post('/v1/chat', async (req, res) => {
  const correlationId = req.requestId;
  const conversationId = req.body?.conversation_id || crypto.randomUUID();
  try {
    const userToken = extractBearer(req.get('authorization'));
    const userClaims = verifyUser(userToken);
    const correlation = req.body?.correlation_id;
    if (correlation && correlation !== correlationId) throw new AgentError('AI_INVALID_REQUEST', 'Correlation id mismatch', 400);
    if (typeof req.body?.message !== 'string' || req.body.message.length < 1 || req.body.message.length > 4000) throw new AgentError('AI_INVALID_REQUEST', 'Message must contain 1-4000 characters', 400);
    const resolvedActor = await withTimeout(
      config.toolTimeoutMs,
      async (signal) => backend.resolveActorContext(userToken, signal, correlationId)
    );
    const actor = validateActor(resolvedActor);
    const tokenUserId = userClaims.userId ?? userClaims.sub;
    if (!tokenUserId || tokenUserId !== actor.userId) {
      throw new AgentError('AI_CONTEXT_INVALID', 'User token does not match the resolved administrator', 401);
    }
    const conversationScope = fairActionScopeKey({ sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId, conversation_id: conversationId });
    const pendingAction = await replay.getPendingAction(conversationScope);
    if ((req.body.operation_id || req.body.operation_version !== undefined) &&
        (!pendingAction || req.body.operation_id !== pendingAction.operationId || req.body.operation_version !== pendingAction.version)) {
      throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió o ya venció', 409);
    }
    if (req.body.operation_intent && (!pendingAction || req.body.operation_id !== pendingAction.operationId || req.body.operation_version !== pendingAction.version)) {
      throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió o ya venció', 409);
    }
    if (req.body.operation_intent === 'update' && pendingAction) {
      if (pendingAction.userId !== actor.userId || pendingAction.organizationId !== actor.organizationId || pendingAction.siteId !== actor.siteId) {
        throw new AgentError('AI_SCOPE_FORBIDDEN', 'La operación no pertenece a este contexto', 403);
      }
      const patch = req.body.operation_data;
      if (!patch || typeof patch !== 'object' || Array.isArray(patch) || Object.keys(patch).some((key) => key.endsWith('_id') || key === 'site_id')) {
        throw new AgentError('AI_INVALID_REQUEST', 'Los datos editados de la operación no son válidos', 400);
      }
      const editablePatch = {};
      for (const [key, rawValue] of Object.entries(patch)) {
        if (rawValue === '') continue;
        const previousValue = pendingAction.proposal[key];
        if (typeof previousValue === 'number') {
          const numericValue = typeof rawValue === 'number' ? rawValue : Number(rawValue);
          if (!Number.isFinite(numericValue)) throw new AgentError('AI_INVALID_REQUEST', `El campo ${key} debe ser numérico`, 400);
          editablePatch[key] = numericValue;
        } else {
          editablePatch[key] = rawValue;
        }
      }
      const proposalInput = { ...pendingAction.proposal, ...editablePatch };
      for (const key of Object.keys(patch)) if (patch[key] === '') delete proposalInput[key];
      const proposal = validateWriteProposal(pendingAction.tool, { ...proposalInput, site_id: actor.siteId }, {
        sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId,
      });
      const updated = {
        ...pendingAction,
        proposal,
        digest: operationDigest(pendingAction.tool, proposal),
        version: pendingAction.version + 1,
      };
      await replay.setPendingAction(conversationScope, updated);
      return res.json({
        success: true,
        conversation_id: conversationId,
        message: { role: 'assistant', content: 'Actualicé la propuesta. Revisa el resumen y confirma para continuar.' },
        ui: pendingActionUi(updated, actor.siteName),
        operation: { id: updated.operationId, version: updated.version, status: 'awaiting_confirmation' },
        citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
      });
    }
    if (isFairCancellation(req.body.message)) {
      if (req.body.operation_id && (!pendingAction || req.body.operation_id !== pendingAction.operationId || req.body.operation_version !== pendingAction.version)) {
        throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió o ya venció', 409);
      }
      await replay.clearPendingAction(conversationScope);
      return res.json({
        success: true,
        conversation_id: conversationId,
        message: { role: 'assistant', content: pendingAction ? `Se canceló la operación “${operationTitles[pendingAction.tool] || 'pendiente'}”. No se realizaron cambios.` : 'No hay una operación pendiente.' },
        ui: { type: 'message', schema: null, data: null, actions: [] },
        operation: pendingAction ? { id: pendingAction.operationId, version: pendingAction.version, status: 'cancelled' } : null,
        citations: [], suggested_actions: [],
        meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
      });
    }
    if (isFairResume(req.body.message) && pendingAction) {
      return res.json({
        success: true,
        conversation_id: conversationId,
        message: { role: 'assistant', content: 'Esta es la propuesta pendiente. Puedes editarla, confirmarla o cancelarla.' },
        ui: pendingActionUi(pendingAction, actor.siteName),
        operation: { id: pendingAction.operationId, version: pendingAction.version, status: 'awaiting_confirmation' },
        citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
      });
    }
    if (isFairResume(req.body.message)) {
      return res.json({
        success: true,
        conversation_id: conversationId,
        message: { role: 'assistant', content: 'No hay una operación pendiente en esta conversación; pudo haber vencido o haberse cancelado.' },
        ui: { type: 'error', schema: null, data: null, actions: [] }, operation: null,
        citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
      });
    }
    if (isFairConfirmation(req.body.message) && pendingAction &&
        pendingAction.userId === actor.userId && pendingAction.organizationId === actor.organizationId &&
        pendingAction.siteId === actor.siteId && pendingAction.digest === operationDigest(pendingAction.tool, pendingAction.proposal)) {
      if (pendingAction.tool === 'createFair' && !pendingAction.proposal.image_url) {
        throw new AgentError('AI_INVALID_REQUEST', 'Sube una portada de feria antes de confirmar', 409);
      }
      if (req.body.operation_id && (req.body.operation_id !== pendingAction.operationId || req.body.operation_version !== pendingAction.version)) {
        throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió; revisa y confirma el resumen actualizado', 409);
      }
      if (!await replay.consumePendingAction(conversationScope, pendingAction.digest)) {
        throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La confirmación ya se usó o venció', 409);
      }
      await withTimeout(config.toolTimeoutMs, async (signal) => backend.assertActiveAssignment(
        { sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId, correlation_id: correlationId },
        actor.siteId,
        userToken,
        signal
      ));
      const fair = await withTimeout(config.toolTimeoutMs, async (signal) => backend.callTool(
        pendingAction.tool, { ...pendingAction.proposal, site_id: actor.siteId },
        { sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId },
        userToken,
        signal
      ));
      return res.json({
        success: true,
        conversation_id: conversationId,
        message: { role: 'assistant', content: `${operationTitles[pendingAction.tool] || 'La operación'} se completó correctamente en CampusVote.` },
        ui: { type: 'success', schema: null, data: fair, actions: [] },
        operation: { id: pendingAction.operationId, version: pendingAction.version, status: 'completed' },
        citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 1, truncated: false },
      });
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
          body: JSON.stringify({ message: req.body.message, conversation_id: conversationId, correlation_id: correlationId }),
          dispatcher: httpsDispatcher,
          signal,
        });
        const payload = await readJsonResponse(upstream, 'python');
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
    return handleError(error, correlationId, res, req, req.startedAt);
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
    const writeTools = new Set(['createFair', 'createFairRubric', 'addFairRubricCriterion', 'createCategoryFairRubric', 'addCategoryFairRubricCriterion', 'createTeachingCriterion', 'updateTeachingCriterion', 'toggleTeachingCriterion', 'deleteTeachingCriterion', 'updateFair', 'changeFairStatus', 'createFairStand', 'updateFairStand', 'deleteFairStand']);
    if (writeTools.has(req.params.tool)) {
      const actionKey = fairActionScopeKey({ ...context, conversation_id: req.body?.conversation_id });
      const existing = await replay.getPendingAction(actionKey);
      const trustedInput = req.params.tool === 'createFair'
        ? preserveUploadedFairImage(input, existing?.tool === 'createFair' ? existing.proposal : null)
        : input;
      const proposal = validateWriteProposal(req.params.tool, trustedInput, context);
      const digest = operationDigest(req.params.tool, proposal);
      const pending = existing?.digest === digest && existing.tool === req.params.tool
        ? existing
        : {
          digest,
          proposal,
          tool: req.params.tool,
          operationId: existing?.operationId || crypto.randomUUID(),
          version: (existing?.version || 0) + 1,
          userId: context.sub,
          organizationId: context.organization_id,
          siteId: context.site_id,
        };
      await replay.setPendingAction(actionKey, pending);
      res.json({ success: true, data: { status: 'confirmation_required', operation_id: pending.operationId, version: pending.version, tool: pending.tool, proposal, site_name: context.site_name } });
      return;
    }
    await withTimeout(config.toolTimeoutMs, async (signal) => backend.assertActiveAssignment(context, siteId, userToken, signal));
    const data = await withTimeout(config.toolTimeoutMs, async (signal) => backend.callTool(req.params.tool, input, context, userToken, signal));
    res.json({ success: true, data, meta: { tool: req.params.tool, correlation_id: correlationId, truncated: false, resource_refs: [] } });
  } catch (error) {
    handleError(error, correlationId, res, req, req.startedAt);
  }
});

app.use((req, res) => {
  const correlationId = req.requestId || crypto.randomUUID();
  const error = new AgentError('AI_RESOURCE_NOT_FOUND', 'No se encontró el recurso solicitado.', 404);
  res.status(404).json(errorResponse(error, correlationId));
});

app.use((error, req, res, _next) => {
  const correlationId = req.requestId || crypto.randomUUID();
  const badJson = error instanceof SyntaxError && Object.hasOwn(error, 'body');
  handleError(badJson
    ? new AgentError('VALIDATION_INVALID_JSON', 'Revisa la información ingresada.', 400)
    : error, correlationId, res, req, req.startedAt);
});

app.listen(config.port, () => console.log(JSON.stringify({ event: 'agent_node_started', port: config.port })));
export { app };
