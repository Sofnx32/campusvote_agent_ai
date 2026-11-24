import crypto from 'node:crypto';
import { config, httpsDispatcher } from '../config.js';
import { AgentError, errorResponse, publicError } from '../errors.js';
import { extractBearer, verifyUser, validateActor, signContext } from '../utils/context.js';
import { readJsonResponse } from '../utils/http-response.js';
import { fairActionScopeKey, operationDigest, isExplicitAffirmation, isFairCancellation, isFairConfirmation, isFairResume, validateWriteProposal } from '../validators/fair-action.js';
import { buildOperationResult, buildFairListView } from '../views/result-view.js';
import { formatCategoriesSummary, formatRubricDetail, formatRubricSummary } from '../views/category-rubric-view.js';
import {
  withTimeout, operationTitles, pendingActionUi, warnIfTokenNearExpiry,
  getOrCreateAdminSession, chatEnvelope,
} from './chat-support.js';

const LIST_TOOLS = new Set([
  'getSiteFairs', 'getSiteProjects', 'getSiteStands', 'getSiteResults',
  'listTeachingCriteria', 'listFairStands', 'listFairCategories', 'listFairCategoryRubrics',
]);

const confirmationChanged = (body, pendingAction) =>
  !pendingAction || body.operation_id !== pendingAction.operationId || body.operation_version !== pendingAction.version;

function editedProposal(patch, pendingAction, actor) {
  if (!patch || typeof patch !== 'object' || Array.isArray(patch) ||
      Object.keys(patch).some((key) => key.endsWith('_id') || key === 'site_id')) {
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
  return validateWriteProposal(pendingAction.tool, { ...proposalInput, site_id: actor.siteId }, {
    sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId, timezone: actor.timezone,
  });
}

async function fetchPythonChat(context, correlationId, conversationId, message) {
  return withTimeout(config.chatTimeoutMs, async (signal) => {
    const upstream = await fetch(new URL('/v1/chat', config.pythonUrl), {
      method: 'POST', headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${config.serviceToken}`,
        'X-AI-Context': context.token, 'X-Correlation-Id': correlationId,
      },
      body: JSON.stringify({ message, conversation_id: conversationId, correlation_id: correlationId }),
      dispatcher: httpsDispatcher,
      signal,
    });
    const payload = await readJsonResponse(upstream, 'python');
    if (!upstream.ok) throw new AgentError(
      payload.error?.code || 'AI_INTERNAL_ERROR',
      payload.error?.message || 'AI service failed',
      upstream.status,
      payload.error?.retryable,
      { details: payload.error?.details },
    );
    return payload;
  });
}

function applyFairListViews(response) {
  const lastTool = response.meta?.last_tool;
  if (!LIST_TOOLS.has(lastTool) || response.ui?.type !== 'message') return;
  const toolData = response.meta?.tool_data;
  if (!toolData || !Array.isArray(toolData)) return;

  // Categorías de feria
  if (lastTool === 'listFairCategories') {
    const fairName = response.meta?.fair_name || 'la feria';
    const message = formatCategoriesSummary(fairName, toolData);
    response.message.content = message;
    response.ui = { type: 'message', schema: null, data: toolData, actions: [] };
    return;
  }

  // Rúbricas por categoría
  if (lastTool === 'listFairCategoryRubrics') {
    const fairName = response.meta?.fair_name || 'la feria';
    // listFairCategoryRubrics devuelve array de categorías con sus rúbricas
    const lines = toolData.map((cat, index) => {
      const rubricBadge = cat.rubrics?.length ? '✅ Con rúbrica' : '⚠️ Sin rúbrica';
      const rubricDetails = cat.rubrics?.map(r => formatRubricSummary(r)).join('\n') || '  _Sin rúbrica_';
      return `${index + 1}. **${cat.name}** (${rubricBadge})\n${rubricDetails}`;
    });
    const message = [
      `### Rúbricas por categoría en ${fairName} (${toolData.length})`,
      ...lines,
    ].join('\n\n');
    response.message.content = message;
    response.ui = { type: 'message', schema: null, data: toolData, actions: [] };
    return;
  }

  // Resto de listados (ferias, proyectos, stands, resultados, criterios docentes, stands de feria)
  const view = buildFairListView({ fairs: toolData, tool: lastTool, totalCount: toolData.length });
  response.message.content = view.message;
  response.ui = view.ui;
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

      warnIfTokenNearExpiry(userClaims, correlationId);
      session = await getOrCreateAdminSession(adminSessions, backend, userToken, userClaims, actor, correlationId);

      const conversationScope = fairActionScopeKey({ sub: actor.userId, organization_id: actor.organizationId, site_id: actor.siteId, conversation_id: conversationId });
      const pendingAction = await replay.getPendingAction(conversationScope);

      if ((req.body.operation_id || req.body.operation_version !== undefined) && confirmationChanged(req.body, pendingAction)) {
        throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió o ya venció', 409);
      }
      if (req.body.operation_intent && confirmationChanged(req.body, pendingAction)) {
        throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió o ya venció', 409);
      }
      if (req.body.operation_intent === 'update' && pendingAction) {
        if (pendingAction.userId !== actor.userId || pendingAction.organizationId !== actor.organizationId || pendingAction.siteId !== actor.siteId) {
          throw new AgentError('AI_SCOPE_FORBIDDEN', 'La operación no pertenece a este contexto', 403);
        }
        const proposal = editedProposal(req.body.operation_data, pendingAction, actor);
        const updated = { ...pendingAction, proposal, digest: operationDigest(pendingAction.tool, proposal), version: pendingAction.version + 1 };
        await replay.setPendingAction(conversationScope, updated);
        return res.json(chatEnvelope(correlationId, conversationId, {
          message: { role: 'assistant', content: 'Actualicé la propuesta. Revisa el resumen y confirma para continuar.' },
          ui: pendingActionUi(updated, actor.siteName),
          operation: { id: updated.operationId, version: updated.version, status: 'awaiting_confirmation' },
        }));
      }
      if (isFairCancellation(req.body.message)) {
        if (req.body.operation_id && confirmationChanged(req.body, pendingAction)) {
          throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió o ya venció', 409);
        }
        await replay.clearPendingAction(conversationScope);
        return res.json(chatEnvelope(correlationId, conversationId, {
          message: { role: 'assistant', content: pendingAction ? `Se canceló la operación “${operationTitles[pendingAction.tool] || 'pendiente'}”. No se realizaron cambios.` : 'No hay una operación pendiente.' },
          ui: { type: 'message', schema: null, data: null, actions: [] },
          operation: pendingAction ? { id: pendingAction.operationId, version: pendingAction.version, status: 'cancelled' } : null,
        }));
      }
      if (isFairResume(req.body.message)) {
        return res.json(chatEnvelope(correlationId, conversationId, pendingAction ? {
          message: { role: 'assistant', content: 'Esta es la propuesta pendiente. Puedes editarla, confirmarla o cancelarla.' },
          ui: pendingActionUi(pendingAction, actor.siteName),
          operation: { id: pendingAction.operationId, version: pendingAction.version, status: 'awaiting_confirmation' },
        } : {
          message: { role: 'assistant', content: 'No hay una operación pendiente en esta conversación; pudo haber vencido o haberse cancelado.' },
          ui: { type: 'error', schema: null, data: null, actions: [] }, operation: null,
        }));
      }
      if ((isFairConfirmation(req.body.message) || (pendingAction && isExplicitAffirmation(req.body.message))) && pendingAction &&
          pendingAction.userId === actor.userId && pendingAction.organizationId === actor.organizationId &&
          pendingAction.siteId === actor.siteId && pendingAction.digest === operationDigest(pendingAction.tool, pendingAction.proposal)) {
        if (pendingAction.tool === 'createFair' && !pendingAction.proposal.image_url) {
          throw new AgentError('AI_INVALID_REQUEST', 'Sube una portada de feria antes de confirmar', 409);
        }
        if (req.body.operation_id && confirmationChanged(req.body, pendingAction)) {
          throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La operación cambió; revisa y confirma el resumen actualizado', 409);
        }
        if (!await replay.consumePendingAction(conversationScope, pendingAction.digest)) {
          throw new AgentError('AI_CONFIRMATION_REQUIRED', 'La confirmación ya se usó o venció', 409);
        }
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
        return res.json(chatEnvelope(correlationId, conversationId, {
          message: { role: 'assistant', content: view.message }, ui: view.ui,
          operation: { id: pendingAction.operationId, version: pendingAction.version, status: 'completed' },
          meta: { correlation_id: correlationId, tool_calls: 1, truncated: false },
        }));
      }

      const deadlineAt = new Date(Date.now() + config.chatTimeoutMs).toISOString();
      const context = signContext(actor, correlationId, deadlineAt);
      await replay.setIssued(context.jti, { correlationId, deadlineAt, userId: actor.userId, adminSessionId: session.sessionId });
      await replay.consume(context.jti, { correlationId });
      try {
        const response = await fetchPythonChat(context, correlationId, conversationId, req.body.message);
        applyFairListViews(response);
        return res.json(response);
      } finally {
        await replay.closeRequest(context.jti);
      }
    } catch (error) {
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

  app.post('/v1/auth/logout', async (req, res) => {
    const correlationId = req.requestId;
    try {
      const userToken = extractBearer(req.get('authorization'));
      const userClaims = verifyUser(userToken);
      const tokenUserId = userClaims.userId ?? userClaims.sub;
      if (!tokenUserId) throw new AgentError('AI_CONTEXT_INVALID', 'User token does not contain user ID', 401);
      const revoked = await adminSessions.revokeSessionByUserId(tokenUserId);
      return res.json({
        success: true, revoked,
        message: revoked ? 'Sesión cerrada correctamente' : 'No había sesión activa',
        requestId: correlationId,
      });
    } catch (error) {
      const normalized = publicError(error);
      console.error(JSON.stringify({
        event: 'logout_failed', service: 'agent-node', requestId: correlationId,
        status: normalized.status, code: normalized.code, cause: error?.name || 'Error',
      }));
      if (!res.headersSent) res.status(normalized.status).json(errorResponse(normalized, correlationId));
    }
  });
}
