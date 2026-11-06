import { config } from './config.js';
import { AgentError } from './errors.js';
import { readJsonResponse } from './http-response.js';

const pathFor = (tool) => config.toolPaths[tool];

export class BackendAdapter {
  constructor(fetchImpl = fetch) {
    this.fetch = fetchImpl;
  }

  async resolveActorContext(userToken, signal, correlationId) {
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
        throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'User session is no longer valid', 401);
      }
      if (response.status === 403) {
        if (code === 'AI_ORGANIZATION_REQUIRED' || code === 'AI_SITE_ASSIGNMENT_REQUIRED') {
          throw new AgentError(code, message || 'The administrator is not assigned to a site', 403);
        }
        throw new AgentError('AI_SCOPE_FORBIDDEN', message || 'Only ADMIN SITE is allowed', 403);
      }
      throw new AgentError(
        'AI_TOOL_UNAVAILABLE',
        'Site context could not be resolved',
        503,
        response.status >= 500
      );
    }
    const payload = await readJsonResponse(response, 'campusvote');
    const data = payload.data ?? payload;
    return {
      userId: data.user_id ?? data.userId ?? data.id,
      role: data.role,
      scopeLevel: data.scope_level ?? data.scopeLevel,
      organizationId: data.organization_id ?? data.organizationId,
      siteId: data.site_id ?? data.siteId,
      siteName: data.site_name ?? data.siteName,
    };
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

  async callTool(tool, input, context, userToken, signal) {
    const path = pathFor(tool);
    if (!config.backendUrl || !path) {
      throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} is not configured against CampusVote`, 503, true);
    }
    const url = new URL(
      path
        .replace(':fairId', encodeURIComponent(input.fair_id || ''))
        .replace(':categoryId', encodeURIComponent(input.category_id || ''))
        .replace(':siteId', encodeURIComponent(input.site_id || ''))
        .replace(':criterionId', encodeURIComponent(input.criterion_id || ''))
        .replace(':standId', encodeURIComponent(input.stand_id || '')),
      config.backendUrl
    );
    const method = config.toolMethods[tool];
    const isWriteTool = [
      'createFairRubric',
      'addFairRubricCriterion',
      'createCategoryFairRubric',
      'addCategoryFairRubricCriterion',
      'createFair',
      'updateFair',
      'changeFairStatus',
      'createFairStand',
      'updateFairStand',
      'deleteFairStand',
      'createTeachingCriterion',
      'updateTeachingCriterion',
      'toggleTeachingCriterion',
      'deleteTeachingCriterion',
    ].includes(tool);
    const directFairTool = [
      'listFairStands',
      'listFairCategories',
      'listFairCategoryRubrics',
      'createFairRubric',
      'addFairRubricCriterion',
      'createCategoryFairRubric',
      'addCategoryFairRubricCriterion',
      'createFair',
      'updateFair',
      'changeFairStatus',
      'createFairStand',
      'updateFairStand',
      'deleteFairStand',
    ].includes(tool);
    const fairScopedTool = [
      'listFairStands', 'listFairCategories', 'listFairCategoryRubrics',
      'createFairRubric', 'addFairRubricCriterion', 'createCategoryFairRubric',
      'addCategoryFairRubricCriterion', 'updateFair', 'changeFairStatus',
      'createFairStand', 'updateFairStand', 'deleteFairStand',
    ].includes(tool);
    const directAcademicTool = [
      'listTeachingCriteria', 'createTeachingCriterion', 'updateTeachingCriterion',
      'toggleTeachingCriterion', 'deleteTeachingCriterion',
    ].includes(tool);
    if (fairScopedTool) {
      await this.assertFairAssignment(input.fair_id, input.site_id, context, userToken, signal);
    }
    const headers = this.headers(context, userToken);
    const request = {
      method,
      headers: method === 'GET' ? headers : { ...headers, 'content-type': 'application/json' },
      signal,
    };
    if (method === 'GET') {
      if (tool === 'listTeachingCriteria') {
        url.searchParams.set('siteId', input.site_id);
      } else if (!directFairTool) {
        url.searchParams.set('site_id', input.site_id);
        url.searchParams.set('organization_id', context.organization_id);
      }
    } else {
      const { fair_id: _fairId, category_id: _categoryId, stand_id: _standId, criterion_id: _criterionId, site_id: _siteId, ...body } = input;
      if (['createTeachingCriterion', 'updateTeachingCriterion'].includes(tool) && tool === 'createTeachingCriterion') body.siteId = context.site_id;
      if (tool === 'createFair' || tool === 'updateFair') body.site_id = context.site_id;
      if (tool === 'updateTeachingCriterion' || tool === 'toggleTeachingCriterion' || tool === 'deleteTeachingCriterion') delete body.criterion_id;
      if (tool !== 'createFair' && tool !== 'updateFair') delete body.site_id;
      request.body = JSON.stringify(
        directFairTool || directAcademicTool ? body : { ...body, organization_id: context.organization_id }
      );
    }
    const response = await this.fetch(url, request);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const backendError = payload?.error ?? {};
      if (response.status >= 500) {
        throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} backend service unavailable`, 503, true);
      }
      const code = response.status === 403
        ? 'AI_SCOPE_FORBIDDEN'
        : response.status === 404
          ? 'AI_RESOURCE_NOT_FOUND'
          : response.status === 409
            ? 'AI_OPERATION_CONFLICT'
            : response.status === 400
              ? 'AI_INVALID_REQUEST'
              : 'AI_BACKEND_REJECTED';
      throw new AgentError(code, backendError.message || `${tool} was rejected by CampusVote`, response.status);
    }
    const result = await readJsonResponse(response, 'campusvote');
    const data = result.data ?? result;
    if (config.requireScopedToolEnvelope && !isWriteTool && !directFairTool && !directAcademicTool) {
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
    const result = await readJsonResponse(response, 'campusvote');
    const fair = result.data ?? result;
    const fairOrganizationId = fair.organization_id ?? fair.organizationId;
    const fairSiteId = fair.site_id ?? fair.siteId ?? fair.site?.id;
    if (
      fairOrganizationId !== context.organization_id ||
      fairSiteId !== siteId ||
      fairSiteId !== context.site_id
    ) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'Fair is not assigned to this administrator', 403);
    }
  }
}
