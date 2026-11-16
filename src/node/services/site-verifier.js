import { config } from '../config.js';
import { AgentError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';

export class SiteVerifier {
  constructor(fetchImpl = fetch) {
    this.fetch = fetchImpl;
  }

  async assertActiveAssignment(context, siteId, userToken, signal) {
    if (context.site_id !== siteId) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not assigned to this administrator', 403);
    }
    if (!config.backendUrl || !config.verifySitePath) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'Active assignment verifier is not configured', 503, true);
    }
    const path = config.verifySitePath
      .replace(':userId', encodeURIComponent(context.sub))
      .replace(':siteId', encodeURIComponent(siteId));
    const verifyUrl = new URL(path, config.backendUrl);
    verifyUrl.searchParams.set('site_id', siteId);
    const response = await this.fetch(verifyUrl, {
      headers: this.headers(context, userToken),
      signal,
    });
    if (!response.ok) {
      throw new AgentError(
        response.status === 403 ? 'AI_SCOPE_FORBIDDEN' : 'AI_TOOL_UNAVAILABLE',
        'Active site assignment could not be verified',
        response.status === 403 ? 403 : 503,
        response.status >= 500
      );
    }
    const result = await readJsonResponse(response, 'campusvote');
    const envelope = result.data ?? result;
    const data = envelope.data ?? envelope;
    if (data.active !== true || data.organization_id !== context.organization_id || data.site_id !== siteId) {
      if (data.active !== true) {
        throw new AgentError('AI_SITE_ASSIGNMENT_REQUIRED', 'The site assignment is inactive, expired, or missing', 403);
      }
      if (data.organization_id !== context.organization_id) {
        throw new AgentError('AI_ORGANIZATION_REQUIRED', 'The site assignment belongs to another organization', 403);
      }
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'The verified site does not match the assigned site', 403);
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