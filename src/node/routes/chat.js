import crypto from 'node:crypto';
import { config } from '../config.js';
import { AgentError, errorResponse, publicError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';
import { extractBearer, verifyUser, validateActor, signContext, verifyContext } from '../utils/context.js';
import { ReplayStore } from '../state/replay-store.js';
import { AdminSessionStore } from '../services/admin-session.js';
import { BackendAdapter } from '../services/backend-adapter.js';
import { fairActionScopeKey, operationDigest, isExplicitAffirmation, isFairCancellation, isFairConfirmation, isFairResume, preserveUploadedFairImage, validateWriteProposal } from '../validators/fair-action.js';
import { buildOperationResult, buildFairListView } from '../views/result-view.js';
import { httpsDispatcher } from '../config.js';

const tools = new Set([
  'getSiteOverview', 'getSiteFairs', 'getSiteProjects', 'getSiteStands', 'getSiteResults',
  'listTeachingCriteria', 'createTeachingCriterion', 'updateTeachingCriterion', 'toggleTeachingCriterion', 'deleteTeachingCriterion',
  'listFairCategories', 'listFairCategoryRubrics', 'createFairRubric', 'addFairRubricCriterion',
  'createCategoryFairRubric', 'addCategoryFairRubricCriterion', 'createFair', 'updateFair', 'changeFairStatus',
  'listFairStands', 'createFairStand', 'updateFairStand', 'deleteFairStand',
]);

const operationTitles = {
  createFair: 'Crear feria', createTeachingCriterion: 'Crear criterio de evaluación docente',
  updateTeachingCriterion: 'Actualizar criterio de evaluación docente', toggleTeachingCriterion: 'Cambiar estado del criterio docente',
  deleteTeachingCriterion: 'Eliminar criterio de evaluación docente', createFairRubric: 'Crear rúbrica de feria',
  addFairRubricCriterion: 'Agregar criterio a la rúbrica', createCategoryFairRubric: 'Crear rúbrica por categoría',
  addCategoryFairRubricCriterion: 'Agregar criterio por categoría', updateFair: 'Actualizar feria',
  changeFairStatus: 'Cambiar estado de feria', createFairStand: 'Crear stand',
  updateFairStand: 'Actualizar stand', deleteFairStand: 'Eliminar stand',
};

const withTimeout = (ms, task) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return Promise.resolve().then(() => task(controller.signal)).finally(() => clearTimeout(timer));
};

function pendingActionUi(pendingAction, siteName) {
  const proposal = pendingAction.proposal || {};
  const fields = pendingAction.tool === 'createFair' ? [
    { name: 'name', type: 'text', label: 'Nombre', required: true, max_length: 200 },
    { name: 'description', type: 'textarea', label: 'Descripción', required: false, max_length: 5000 },
    { name: 'starts_at', type: 'datetime', label: 'Inicio (fecha y hora)', required: false },
    { name: 'ends_at', type: 'datetime', label: 'Fin (fecha y hora)', required: false },
    { name: 'registration_deadline', type: 'datetime', label: 'Límite de registro de proyectos', required: false },
    { name: 'image_url', type: 'image', label: 'Portada', required: true },
  ] : Object.entries(proposal)
    .filter(([key]) => !key.endsWith('_id') && key !== 'site_id')
    .map(([key, value]) => ({
      name: key,
      type: key === 'image_url' ? 'image'
        : ['starts_at', 'ends_at', 'registration_deadline'].includes(key) ? 'datetime'
        : ['min_score', 'max_score', 'points', 'position'].includes(key) ? 'number'
        : key === 'description' ? 'textarea'
        : key === 'status' ? 'select' : 'text',
      label: key.replaceAll('_', ' '),
      required: false,
      ...(key === 'description' ? { max_length: 5000 } : {}),
      ...(key === 'name' ? { max_length: pendingAction.tool === 'createFair' ? 200 : 255 } : {}),
      ...(key === 'status' ? { options: ['DRAFT', 'OPEN', 'CLOSED'].map((s) => ({ value: s, label: s })) } : {}),
    }));
  return {
    type: 'confirmation',
    schema: { title: operationTitles[pendingAction.tool] || 'Revisar operación', fields },
    data: { ...Object.fromEntries(Object.entries(proposal).filter(([key]) => !key.endsWith('_id') && key !== 'site_id')), site_name: siteName },
    actions: ['confirm', 'edit', 'cancel'],
  };
}

const requireServiceToken = (req) => {
  if (req.get('authorization') !== `Bearer ${config.serviceToken}`) throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'Invalid service token', 401);
};

async function getOrCreateAdminSession(adminSessions, userToken, userClaims, actor, correlationId) {
  const tokenUserId = userClaims.userId ?? userClaims.sub;
  let session = await adminSessions.getSessionByUserId(tokenUserId);

  if (session) {
    // Validate session matches current context
    if (session.userId !== actor.userId ||
        session.organizationId !== actor.organizationId ||
        session.siteId !== actor.siteId) {
      // Context mismatch - revoke old session and create new one
      await adminSessions.revokeSession(session.sessionId);
      session = null;
    }
  }

  if (!session) {
    // Create new admin session
    session = await adminSessions.createSession(
      actor.userId,
      actor.organizationId,
      actor.siteId,
      actor.siteName,
      actor.timezone,
      userToken,
      correlationId
    );
  } else {
    // Update last activity
    await adminSessions.updateActivity(session.sessionId);
  }

  // Validate session status
  if (session.status !== 'active') {
    if (session.status === 'revoked') {
      throw new AgentError('SESSION_REVOKED', 'Admin session has been revoked', 401);
    }
    throw new AgentError('SESSION_EXPIRED', 'Admin session has expired', 401);
  }

  // Check if CampusVote user token is still valid by resolving actor context
  // This ensures the user's CampusVote session hasn't expired
  try {
    await withTimeout(config.toolTimeoutMs, async (signal) => backend.resolveActorContext(session.userToken, signal, correlationId));
  } catch (error) {
    if (error.code === 'AI_SERVICE_UNAUTHORIZED') {
      // User's CampusVote session expired
      await adminSessions.revokeSession(session.sessionId);
      throw new AgentError('SESSION_EXPIRED', 'User session has expired', 401);
    }
    // Other errors (network, etc.) - allow session to continue, will fail on actual operations
  }

  return session;
}

export function registerChatRoutes(app, replay, backend, adminSessions) {
  app.post('/v1/chat', async (req, res) => {
    const correlationId = req.requestId;
    const conversationId = req.body?.conversation_id || crypto.randomUUID();
    let session = null;
    try {
      const userToken = extractBearer(req.get('authorization'));
      const userClaims = verifyUser(userToken);
      const correlation = req.body?.correlation_id;
      if (correlation && correlation !== correlationId) throw new AgentError('AI_INVALID_REQUEST', 'Correlation id mismatch', 400);
      if (typeof req.body?.message !== 'string' || req.body.message.length < 1 || req.body.message.length > 4000) throw new AgentError('AI_INVALID_REQUEST', 'Message must contain 1-4000 characters', 400);
      const resolvedActor = await withTimeout(config.toolTimeoutMs, async (signal) => backend.resolveActorContext(userToken, signal, correlationId));
      const actor = validateActor(resolvedActor);
      const tokenUserId = userClaims.userId ?? userClaims.sub;
      if (!tokenUserId || tokenUserId !== actor.userId) throw new AgentError('AI_CONTEXT_INVALID', 'User token does not match the resolved administrator', 401);

      // Get or create admin session
      session = await getOrCreateAdminSession(adminSessions, userToken, userClaims, actor, correlationId);

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
          sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId, timezone: actor.timezone,
        });
        const updated = { ...pendingAction, proposal, digest: operationDigest(pendingAction.tool, proposal), version: pendingAction.version + 1 };
        await replay.setPendingAction(conversationScope, updated);
        return res.json({
          success: true, conversation_id: conversationId,
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
          success: true, conversation_id: conversationId,
          message: { role: 'assistant', content: pendingAction ? `Se canceló la operación “${operationTitles[pendingAction.tool] || 'pendiente'}”. No se realizaron cambios.` : 'No hay una operación pendiente.' },
          ui: { type: 'message', schema: null, data: null, actions: [] },
          operation: pendingAction ? { id: pendingAction.operationId, version: pendingAction.version, status: 'cancelled' } : null,
          citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
        });
      }
      if (isFairResume(req.body.message) && pendingAction) {
        return res.json({
          success: true, conversation_id: conversationId,
          message: { role: 'assistant', content: 'Esta es la propuesta pendiente. Puedes editarla, confirmarla o cancelarla.' },
          ui: pendingActionUi(pendingAction, actor.siteName),
          operation: { id: pendingAction.operationId, version: pendingAction.version, status: 'awaiting_confirmation' },
          citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
        });
      }
      if (isFairResume(req.body.message)) {
        return res.json({
          success: true, conversation_id: conversationId,
          message: { role: 'assistant', content: 'No hay una operación pendiente en esta conversación; pudo haber vencido o haberse cancelado.' },
          ui: { type: 'error', schema: null, data: null, actions: [] }, operation: null,
          citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
        });
      }
      if ((isFairConfirmation(req.body.message) || (pendingAction && isExplicitAffirmation(req.body.message))) && pendingAction &&
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
        // Re-verify active assignment before executing
        await withTimeout(config.toolTimeoutMs, async (signal) => backend.assertActiveAssignment(
          { sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId, correlation_id: correlationId },
          actor.siteId, session.userToken, signal
        ));
        const result = await withTimeout(config.toolTimeoutMs, async (signal) => backend.callTool(
          pendingAction.tool, { ...pendingAction.proposal, site_id: actor.siteId },
          { sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId },
          session.userToken, signal
        ));
        const view = buildOperationResult({
          tool: pendingAction.tool, data: result, proposal: pendingAction.proposal, operationId: pendingAction.operationId,
        });
        return res.json({
          success: true, conversation_id: conversationId,
          message: { role: 'assistant', content: view.message }, ui: view.ui,
          operation: { id: pendingAction.operationId, version: pendingAction.version, status: 'completed' },
          citations: [], suggested_actions: [], meta: { correlation_id: correlationId, tool_calls: 1, truncated: false },
        });
      }
      const deadlineAt = new Date(Date.now() + config.chatTimeoutMs).toISOString();
      const context = signContext(actor, correlationId, deadlineAt);
      await replay.setIssued(context.jti, { correlationId, deadlineAt, userId: actor.userId, adminSessionId: session.sessionId });
      await replay.consume(context.jti, { correlationId });
      try {
        const response = await withTimeout(config.chatTimeoutMs, async (signal) => {
          const upstream = await fetch(new URL('/v1/chat', config.pythonUrl), {
            method: 'POST', headers: {
              'content-type': 'application/json',
              authorization: `Bearer ${config.serviceToken}`,
              'X-AI-Context': context.token, 'X-Correlation-Id': correlationId,
            },
            body: JSON.stringify({ message: req.body.message, conversation_id: conversationId, correlation_id: correlationId }),
            dispatcher: (await import('../config.js')).httpsDispatcher,
            signal,
          });
          const payload = await readJsonResponse(upstream, 'python');
          if (!upstream.ok) throw new AgentError(payload.error?.code || 'AI_INTERNAL_ERROR', payload.error?.message || 'AI service failed', upstream.status, payload.error?.retryable);
          return payload;
        });
        // Transformar respuestas de herramientas de listado (getSiteFairs, etc.) a vista de tarjetas
        const lastTool = response.meta?.last_tool;
        const listTools = new Set(['getSiteFairs', 'getSiteProjects', 'getSiteStands', 'getSiteResults', 'listTeachingCriteria', 'listFairStands', 'listFairCategories', 'listFairCategoryRubrics']);
        if (listTools.has(lastTool) && response.ui?.type === 'message') {
          // Para herramientas de listado, Python incluye los datos crudos en meta.tool_data
          const toolData = response.meta?.tool_data;
          if (toolData && Array.isArray(toolData)) {
            const view = buildFairListView({ fairs: toolData, tool: lastTool, totalCount: toolData.length });
            response.message.content = view.message;
            response.ui = view.ui;
          }
        }
        return res.json(response);
      } finally {
        await replay.closeRequest(context.jti);
      }
    } catch (error) {
      // If session was revoked/expired during request, ensure cleanup
      if (session?.sessionId && (error.code === 'SESSION_REVOKED' || error.code === 'SESSION_EXPIRED')) {
        await adminSessions.revokeSession(session.sessionId);
      }
      const normalized = publicError(error);
      console.error(JSON.stringify({
        event: 'request_failed', service: 'agent-node', requestId: correlationId,
        method: req?.method, route: req?.route?.path || req?.path,
        status: normalized.status, code: normalized.code,
        cause: error?.name || 'Error', durationMs: req.startedAt ? Date.now() - req.startedAt : undefined,
      }));
      if (!res.headersSent) res.status(normalized.status).json(errorResponse(normalized, correlationId));
    }
  });

  // Logout endpoint - revokes admin session
  app.post('/v1/auth/logout', async (req, res) => {
    const correlationId = req.requestId;
    try {
      const userToken = extractBearer(req.get('authorization'));
      const userClaims = verifyUser(userToken);
      const tokenUserId = userClaims.userId ?? userClaims.sub;
      if (!tokenUserId) {
        throw new AgentError('AI_CONTEXT_INVALID', 'User token does not contain user ID', 401);
      }
      const revoked = await adminSessions.revokeSessionByUserId(tokenUserId);
      return res.json({
        success: true,
        revoked,
        message: revoked ? 'Sesión cerrada correctamente' : 'No había sesión activa',
        requestId: correlationId,
      });
    } catch (error) {
      const normalized = publicError(error);
      console.error(JSON.stringify({
        event: 'logout_failed', service: 'agent-node', requestId: correlationId,
        status: normalized.status, code: normalized.code,
        cause: error?.name || 'Error',
      }));
      if (!res.headersSent) res.status(normalized.status).json(errorResponse(normalized, correlationId));
    }
  });
}