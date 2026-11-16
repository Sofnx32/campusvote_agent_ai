import { config } from '../config.js';
import { AgentError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';

const pathFor = (tool) => config.toolPaths[tool];
const toolMethod = (tool) => config.toolMethods[tool];

const WRITE_TOOLS = new Set([
  'createFairRubric', 'addFairRubricCriterion', 'createCategoryFairRubric', 'addCategoryFairRubricCriterion',
  'createFair', 'updateFair', 'changeFairStatus', 'createFairStand', 'updateFairStand', 'deleteFairStand',
  'createTeachingCriterion', 'updateTeachingCriterion', 'toggleTeachingCriterion', 'deleteTeachingCriterion',
]);

const DIRECT_FAIR_TOOLS = new Set([
  'listFairStands', 'listFairCategories', 'listFairCategoryRubrics',
  'createFairRubric', 'addFairRubricCriterion', 'createCategoryFairRubric',
  'addCategoryFairRubricCriterion', 'updateFair', 'changeFairStatus',
  'createFairStand', 'updateFairStand', 'deleteFairStand',
  'createFair',
]);

const DIRECT_ACADEMIC_TOOLS = new Set([
  'listTeachingCriteria', 'createTeachingCriterion', 'updateTeachingCriterion',
  'toggleTeachingCriterion', 'deleteTeachingCriterion',
]);

const FAIR_SCOPED_TOOLS = new Set([
  'listFairStands', 'listFairCategories', 'listFairCategoryRubrics',
  'createFairRubric', 'addFairRubricCriterion', 'createCategoryFairRubric',
  'addCategoryFairRubricCriterion', 'updateFair', 'changeFairStatus',
  'createFairStand', 'updateFairStand', 'deleteFairStand',
]);

const FORBIDDEN_FIELDS = /"(?:(?:vote|votes|rubric|rubrics|score|scores|evaluation|evaluations|jury_user_id|user_id))"/i;

export class ToolExecutor {
  constructor(fetchImpl = fetch) {
    this.fetch = fetchImpl;
  }

  async callTool(tool, input, context, userToken, signal, siteVerifier, fairVerifier) {
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
    const method = toolMethod(tool);
    const isWriteTool = WRITE_TOOLS.has(tool);
    const directFairTool = DIRECT_FAIR_TOOLS.has(tool);
    const directAcademicTool = DIRECT_ACADEMIC_TOOLS.has(tool);
    const fairScopedTool = FAIR_SCOPED_TOOLS.has(tool);

    if (fairScopedTool) {
      await fairVerifier.assertFairAssignment(input.fair_id, input.site_id, context, userToken, signal);
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
      if (tool === 'createTeachingCriterion') body.siteId = context.site_id;
      if (tool === 'createFair' || tool === 'updateFair') body.site_id = context.site_id;
      if (['updateTeachingCriterion', 'toggleTeachingCriterion', 'deleteTeachingCriterion'].includes(tool)) delete body.criterion_id;
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
      const backendMessage = typeof backendError.message === 'string' && backendError.message.trim()
        ? backendError.message.trim().slice(0, 300)
        : undefined;
      const details = backendError.details && typeof backendError.details === 'object' ? backendError.details : undefined;
      throw new AgentError(code, backendError.message || `${tool} was rejected by CampusVote`, response.status, false, {
        ...(backendMessage && response.status < 500 ? { publicMessage: backendMessage } : {}),
        ...(details ? { details } : {}),
      });
    }

    const result = await readJsonResponse(response, 'campusvote');
    const data = result.data ?? result;

    if (config.requireScopedToolEnvelope && !isWriteTool && !directFairTool && !directAcademicTool) {
      if (data.organization_id !== context.organization_id || data.site_id !== input.site_id) {
        throw new AgentError('AI_TOOL_UNAVAILABLE', `${tool} does not provide a verified organization/site response envelope`, 503, true);
      }
    }

    const serialized = JSON.stringify(data);
    if (!isWriteTool && FORBIDDEN_FIELDS.test(serialized)) {
      throw new AgentError('AI_RESPONSE_INVALID', 'CampusVote response contains individual protected data', 502);
    }

    const bytes = Buffer.byteLength(serialized);
    if (bytes > config.maxToolBytes) {
      throw new AgentError('AI_INVALID_REQUEST', 'Tool response exceeds aggregate data budget', 400);
    }

    return data;
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