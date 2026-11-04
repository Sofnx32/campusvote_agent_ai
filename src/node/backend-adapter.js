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
    const url = new URL(
      path
        .replace(':fairId', encodeURIComponent(input.fair_id || ''))
        .replace(':siteId', encodeURIComponent(input.site_id || ''))
        .replace(':standId', encodeURIComponent(input.stand_id || '')),
      config.backendUrl
    );
    const method = config.toolMethods[tool];
    const isWriteTool = [
      'createFairRubric',
      'addFairRubricCriterion',
      'createFair',
      'updateFair',
      'changeFairStatus',
      'createFairStand',
      'updateFairStand',
      'deleteFairStand',
    ].includes(tool);
    const directFairTool = [
      'listFairStands',
      'createFairRubric',
      'addFairRubricCriterion',
      'createFair',
      'updateFair',
      'changeFairStatus',
      'createFairStand',
      'updateFairStand',
      'deleteFairStand',
    ].includes(tool);
    if ((isWriteTool && tool !== 'createFair') || tool === 'listFairStands') {
      await this.assertFairAssignment(input.fair_id, input.site_id, context, userToken, signal);
    }
    const headers = this.headers(context, userToken);
    const request = {
      method,
      headers: method === 'GET' ? headers : { ...headers, 'content-type': 'application/json' },
      signal,
    };
    if (method === 'GET') {
      if (!directFairTool) {
        url.searchParams.set('site_id', input.site_id);
        url.searchParams.set('organization_id', context.organization_id);
      }
    } else {
      const { fair_id: _fairId, stand_id: _standId, ...body } = input;
      if (tool !== 'createFair' && tool !== 'updateFair') delete body.site_id;
      request.body = JSON.stringify(
        directFairTool ? body : { ...body, organization_id: context.organization_id }
      );
    }
    const response = await this.fetch(url, request);
    if (!response.ok) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} backend service unavailable`, 503, true);
    }
    const result = await response.json();
    const data = result.data ?? result;
    if (config.requireScopedToolEnvelope && !isWriteTool && !directFairTool) {
      if (data.organization_id !== context.organization_id || data.site_id !== input.site_id) {
        throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} does not provide a verified organization/site response envelope`, 503, true);
      }
    }
    const serialized = JSON.stringify(data);
    const forbidden = /"(?:(?:vote|votes|rubric|rubrics|score|scores|evaluation|evaluations|jury_user_id|user_id))"/i;
    if (!isWriteTool && forbidden.test(serialized)) {
      throw new AgentError('AI_RESPONSE_INVALID', 'CampusVote response contains individual protected data', 502);
    }
    const bytes = Buffer.byteLength(serialized);
    if (bytes > config.maxToolBytes) {
      throw new AgentError('AI_INVALID_REQUEST', 'Tool response exceeds aggregate data budget', 400);
    }
    return data;
  }

  async assertFairAssignment(fairId, siteId, context, userToken, signal) {
    if (!fairId || !config.verifyFairPath) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', 'Fair assignment verifier is not configured', 503, true);
    }
    const path = config.verifyFairPath
      .replace(':fairId', encodeURIComponent(fairId));
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
    const result = await response.json();
    const fair = result.data ?? result;
    const fairOrganizationId = fair.organization_id ?? fair.organizationId;
    const fairSiteId = fair.site_id ?? fair.siteId;
    if (
      fairOrganizationId !== context.organization_id ||
      fairSiteId !== siteId ||
      !context.site_ids.includes(fairSiteId)
    ) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'Fair is not assigned to this administrator', 403);
    }
  }
}
