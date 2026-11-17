import crypto from 'node:crypto';
import { config } from '../config.js';
import { AgentError, errorResponse } from '../errors.js';
import { verifyContext } from '../utils/context.js';
import { ReplayStore } from '../state/replay-store.js';
import { AdminSessionStore } from '../services/admin-session.js';
import { BackendAdapter } from '../services/backend-adapter.js';
import { fairActionScopeKey, operationDigest, validateWriteProposal, preserveUploadedFairImage } from '../validators/fair-action.js';

const tools = new Set([
  'getSiteOverview', 'getSiteFairs', 'getSiteProjects', 'getSiteStands', 'getSiteResults',
  'listTeachingCriteria', 'createTeachingCriterion', 'updateTeachingCriterion', 'toggleTeachingCriterion', 'deleteTeachingCriterion',
  'listFairCategories', 'listFairCategoryRubrics', 'createFairRubric', 'addFairRubricCriterion',
  'createCategoryFairRubric', 'addCategoryFairRubricCriterion', 'createFair', 'updateFair', 'changeFairStatus',
  'listFairStands', 'createFairStand', 'updateFairStand', 'deleteFairStand',
]);

const writeTools = new Set([
  'createFair', 'createFairRubric', 'addFairRubricCriterion', 'createCategoryFairRubric', 'addCategoryFairRubricCriterion',
  'createTeachingCriterion', 'updateTeachingCriterion', 'toggleTeachingCriterion', 'deleteTeachingCriterion',
  'updateFair', 'changeFairStatus', 'createFairStand', 'updateFairStand', 'deleteFairStand',
]);

const withTimeout = (ms, task) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return Promise.resolve().then(() => task(controller.signal)).finally(() => clearTimeout(timer));
};

export function registerToolsRoutes(app, replay, backend, adminSessions) {
  app.post('/internal/ai/v1/tools/:tool', async (req, res) => {
    const correlationId = req.get('X-Correlation-Id') || req.body?.correlation_id;
    try {
      if (req.get('authorization') !== `Bearer ${config.serviceToken}`) throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'Invalid service token', 401);
      if (!tools.has(req.params.tool)) throw new AgentError('AI_TOOL_NOT_FOUND', 'Tool is not allowlisted', 404);
      const context = verifyContext(req.get('X-AI-Context'));
      if (context.correlation_id !== correlationId || req.body?.correlation_id !== correlationId) throw new AgentError('AI_INVALID_REQUEST', 'Correlation id mismatch', 400);
      if (typeof req.body?.tool_call_id !== 'string' || !req.body.tool_call_id) throw new AgentError('AI_INVALID_REQUEST', 'tool_call_id is required', 400);
      const siteId = req.body?.input?.site_id;
      if (typeof siteId !== 'string' || siteId !== context.site_id) throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not authorized', 403);
      await replay.reserveTool(context.jti, req.body.tool_call_id, config.maxToolCalls);

      // Retrieve admin session to get the user token
      const issued = await replay.getIssuedContext(context.jti);
      if (!issued?.adminSessionId) {
        throw new AgentError('AI_CONTEXT_REPLAYED', 'Original admin session not found', 409);
      }
      const adminSession = await adminSessions.getSession(issued.adminSessionId);
      if (!adminSession || adminSession.status !== 'active') {
        throw new AgentError(adminSession?.status === 'revoked' ? 'SESSION_REVOKED' : 'SESSION_EXPIRED', 'Admin session is no longer active', 401);
      }
      // Validate session matches context
      if (adminSession.organizationId !== context.organization_id || adminSession.siteId !== context.site_id) {
        throw new AgentError('AI_SCOPE_MISMATCH', 'Admin session does not match AI context scope', 403);
      }
      const userToken = adminSession.userToken;
      const input = { ...(req.body?.input ?? {}), site_id: siteId };

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
              digest, proposal, tool: req.params.tool,
              operationId: existing?.operationId || crypto.randomUUID(),
              version: (existing?.version || 0) + 1,
              userId: context.sub, organizationId: context.organization_id, siteId: context.site_id,
            };
        await replay.setPendingAction(actionKey, pending);
        return res.json({ success: true, data: { status: 'confirmation_required', operation_id: pending.operationId, version: pending.version, tool: pending.tool, proposal, site_name: context.site_name } });
      }
      await withTimeout(config.toolTimeoutMs, async (signal) => backend.assertActiveAssignment(context, siteId, userToken, signal));
      const data = await withTimeout(config.toolTimeoutMs, async (signal) => backend.callTool(req.params.tool, input, context, userToken, signal));
      res.json({ success: true, data, meta: { tool: req.params.tool, correlation_id: correlationId, truncated: false, resource_refs: [] } });
    } catch (error) {
      const normalized = error instanceof AgentError ? error : { code: 'AI_INTERNAL_ERROR', message: 'Internal error', status: 500 };
      console.error(JSON.stringify({
        event: 'tool_failed', service: 'agent-node', requestId: correlationId,
        tool: req.params.tool, status: normalized.status, code: normalized.code,
        cause: error?.name || 'Error',
      }));
      if (!res.headersSent) res.status(normalized.status || 500).json(errorResponse(normalized, correlationId));
    }
  });
}