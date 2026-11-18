import { config } from '../config.js';
import { AgentError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';

export const withTimeout = (ms, task) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return Promise.resolve().then(() => task(controller.signal)).finally(() => clearTimeout(timer));
};

export const operationTitles = {
  createFair: 'Crear feria', createTeachingCriterion: 'Crear criterio de evaluación docente',
  updateTeachingCriterion: 'Actualizar criterio de evaluación docente', toggleTeachingCriterion: 'Cambiar estado del criterio docente',
  deleteTeachingCriterion: 'Eliminar criterio de evaluación docente', createFairRubric: 'Crear rúbrica de feria',
  addFairRubricCriterion: 'Agregar criterio a la rúbrica', createCategoryFairRubric: 'Crear rúbrica por categoría',
  addCategoryFairRubricCriterion: 'Agregar criterio por categoría', updateFair: 'Actualizar feria',
  changeFairStatus: 'Cambiar estado de feria', createFairStand: 'Crear stand',
  updateFairStand: 'Actualizar stand', deleteFairStand: 'Eliminar stand',
};

export function pendingActionUi(pendingAction, siteName) {
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

export function warnIfTokenNearExpiry(userClaims, correlationId) {
  const exp = userClaims.exp;
  if (typeof exp !== 'number') return;
  const timeToExpireMs = exp * 1000 - Date.now();
  if (timeToExpireMs < 5 * 60 * 1000) {
    console.warn(JSON.stringify({
      event: 'user_token_near_expiry', service: 'agent-node', requestId: correlationId,
      timeToExpireMs,
    }));
  }
}

export async function getOrCreateAdminSession(adminSessions, backend, userToken, userClaims, actor, correlationId) {
  const tokenUserId = userClaims.userId ?? userClaims.sub;
  let session = await adminSessions.getSessionByUserId(tokenUserId);

  if (session) {
    if (session.userId !== actor.userId ||
        session.organizationId !== actor.organizationId ||
        session.siteId !== actor.siteId) {
      await adminSessions.revokeSession(session.sessionId);
      session = null;
    }
  }

  if (!session) {
    session = await adminSessions.createSession(
      actor.userId, actor.organizationId, actor.siteId,
      actor.siteName, actor.timezone, userToken, correlationId
    );
  } else {
    await adminSessions.updateActivity(session.sessionId);
  }

  if (session.status !== 'active') {
    if (session.status === 'revoked') {
      throw new AgentError('SESSION_REVOKED', 'Admin session has been revoked', 401);
    }
    throw new AgentError('SESSION_EXPIRED', 'Admin session has expired', 401);
  }

  try {
    await withTimeout(config.toolTimeoutMs, async (signal) => backend.resolveActorContext(session.userToken, signal, correlationId));
  } catch (error) {
    if (error.code === 'AI_SERVICE_UNAUTHORIZED') {
      await adminSessions.revokeSession(session.sessionId);
      throw new AgentError('SESSION_EXPIRED', 'User session has expired', 401);
    }
  }

  return session;
}

export const chatEnvelope = (correlationId, conversationId, overrides = {}) => ({
  success: true,
  conversation_id: conversationId,
  citations: [],
  suggested_actions: [],
  meta: { correlation_id: correlationId, tool_calls: 0, truncated: false },
  ...overrides,
});
