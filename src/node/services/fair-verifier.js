import { config } from '../config.js';
import { AgentError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';

export class FairVerifier {
  constructor(fetchImpl = fetch) {
    this.fetch = fetchImpl;
  }

  async assertFairAssignment(fairId, siteId, context, userToken, signal) {
    if (!fairId || !config.verifyFairPath) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'Fair assignment verifier is not configured', 503, true);
    }
    const path = config.verifyFairPath.replace(':fairId', encodeURIComponent(fairId));
    const response = await this.fetch(new URL(path, config.backendUrl), {
      headers: this.headers(context, userToken),
      signal,
    });
    if (!response.ok) {
      throw new AgentError(
        response.status === 403 || response.status === 404 ? 'AI_SCOPE_FORBIDDEN' : 'AI_TOOL_UNAVAILABLE',
        'Fair assignment could not be verified',
        response.status === 403 || response.status === 404 ? response.status : 503,
        response.status >= 500
      );
    }
    const result = await readJsonResponse(response, 'campusvote');
    const fair = result.data ?? result;
    const fairOrganizationId = fair.organization_id ?? fair.organizationId;
    const fairSiteId = fair.site_id ?? fair.siteId ?? fair.site?.id;
    if (fairOrganizationId !== context.organization_id || fairSiteId !== siteId || fairSiteId !== context.site_id) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'Fair is not assigned to this administrator', 403);
    }
  }

  headers(context, userToken) {
    if (config.backendAuthMode !== 'user_jwt' || !userToken) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'CampusVote authentication mode is not configured', 503, true);
    }
    return {
      authorization: `Bearer ${userToken}`,
      'X-AI-Organization-Id': context.organization_id,
      'X-AI-Site-Id': context.site_id,
      ...(context.correlation_id ? { 'X-Correlation-Id': context.correlation_id } : {}),
    };
  }
}