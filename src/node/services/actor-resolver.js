import { config } from '../config.js';
import { AgentError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';

export class ActorResolver {
  constructor(fetchImpl = fetch) {
    this.fetch = fetchImpl;
  }

  async resolve(userToken, signal, correlationId) {
    if (!config.backendUrl || !config.resolveContextPath) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'Site context resolver is not configured', 503, true);
    }
    if (config.backendAuthMode !== 'user_jwt' || !userToken) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'CampusVote authentication mode is not configured', 503, true);
    }
    const response = await this.fetch(new URL(config.resolveContextPath, config.backendUrl), {
      headers: { authorization: `Bearer ${userToken}`, ...(correlationId ? { 'X-Correlation-Id': correlationId } : {}) },
      signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const code = payload?.error?.code;
      const message = payload?.error?.message;
      if (response.status === 401) {
        const reason = code || 'UNKNOWN';
        console.warn(JSON.stringify({
          event: 'campusvote_auth_failed', service: 'agent-node', requestId: correlationId,
          reason, status: 401,
        }));
        const err = new AgentError('AI_SERVICE_UNAUTHORIZED', 'User session is no longer valid', 401);
        err.details = { reason };
        throw err;
      }
      if (response.status === 403) {
        if (code === 'AI_ORGANIZATION_REQUIRED' || code === 'AI_SITE_ASSIGNMENT_REQUIRED') {
          throw new AgentError(code, message || 'The administrator is not assigned to a site', 403);
        }
        throw new AgentError('AI_SCOPE_FORBIDDEN', message || 'Only ADMIN SITE is allowed', 403);
      }
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'Site context could not be resolved', 503, response.status >= 500);
    }
    const payload = await readJsonResponse(response, 'campusvote');
    const data = payload.data ?? payload;
    const timezone = data.timezone ?? data.time_zone;
    return {
      userId: data.user_id ?? data.userId ?? data.id,
      role: data.role,
      scopeLevel: data.scope_level ?? data.scopeLevel,
      organizationId: data.organization_id ?? data.organizationId,
      siteId: data.site_id ?? data.siteId,
      siteName: data.site_name ?? data.siteName,
      ...(typeof timezone === 'string' && timezone.trim() ? { timezone: timezone.trim() } : {}),
    };
  }
}