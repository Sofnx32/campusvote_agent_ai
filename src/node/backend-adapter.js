import { config } from './config.js';
import { AgentError } from './errors.js';

const pathFor = (tool) => config.toolPaths[tool];

export class BackendAdapter {
  constructor(fetchImpl = fetch) {
    this.fetch = fetchImpl;
  }

  async assertActiveAssignment(context, siteId, userToken, signal) {
    if (!context.site_ids.includes(siteId)) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not assigned to this administrator', 403);
    }
    if (!config.backendUrl || !config.verifySitePath) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'Active assignment verifier is not configured', 503, true);
    }
    const path = config.verifySitePath
      .replace(':userId', encodeURIComponent(context.sub))
      .replace(':siteId', encodeURIComponent(siteId));
    const response = await this.fetch(new URL(path, config.backendUrl), {
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
    const result = await response.json();
    const data = result.data ?? result;
    if (data.active !== true || data.organization_id !== context.organization_id || data.site_id !== siteId) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'Active site assignment is invalid', 403);
    }
  }

  headers(context, userToken) {
    if (config.backendAuthMode !== 'user_jwt' || !userToken) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'CampusVote authentication mode is not configured', 503, true);
    }
    return {
      authorization: `Bearer ${userToken}`,
      'X-AI-Organization-Id': context.organization_id,
      'X-AI-Site-Id': context.site_ids.join(','),
    };
  }

  async callTool(tool, input, context, userToken, signal) {
    const path = pathFor(tool);
    if (!config.backendUrl || !path) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} is not configured against CampusVote`, 503, true);
    }
    const url = new URL(path, config.backendUrl);
    const method = config.toolMethods[tool];
    const headers = this.headers(context, userToken);
    const request = {
      method,
      headers: method === 'GET' ? headers : { ...headers, 'content-type': 'application/json' },
      signal,
    };
    if (method === 'GET') {
      url.searchParams.set('site_id', input.site_id);
      url.searchParams.set('organization_id', context.organization_id);
    } else {
      request.body = JSON.stringify({ site_id: input.site_id, organization_id: context.organization_id });
    }
    const response = await this.fetch(url, request);
    if (!response.ok) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} backend service unavailable`, 503, true);
    }
    const result = await response.json();
    const data = result.data ?? result;
    if (config.requireScopedToolEnvelope) {
      if (data.organization_id !== context.organization_id || data.site_id !== input.site_id) {
        throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} does not provide a verified organization/site response envelope`, 503, true);
      }
    }
    const serialized = JSON.stringify(data);
    const forbidden = /"(?:(?:vote|votes|rubric|rubrics|score|scores|evaluation|evaluations|jury_user_id|user_id))"/i;
    if (forbidden.test(serialized)) {
      throw new AgentError('AI_RESPONSE_INVALID', 'CampusVote response contains individual protected data', 502);
    }
    const bytes = Buffer.byteLength(serialized);
    if (bytes > config.maxToolBytes) {
      throw new AgentError('AI_INVALID_REQUEST', 'Tool response exceeds aggregate data budget', 400);
    }
    return data;
  }
}
