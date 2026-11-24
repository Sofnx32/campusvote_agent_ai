import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentError, errorResponse, publicError } from '../../src/node/errors.js';

test('error contract keeps status, stable code and requestId without leaking technical cause', () => {
  const body = errorResponse(publicError(new Error('postgres password=secret')), 'req-123');
  assert.equal(body.success, false);
  assert.equal(body.error.code, 'AI_INTERNAL_ERROR');
  assert.equal(body.error.message, 'Ocurrió un problema inesperado.');
  assert.equal(body.error.requestId, 'req-123');
  assert.equal(body.error.details, null);
  assert.equal(JSON.stringify(body).includes('secret'), false);
});

test('known provider, timeout, authorization and Redis errors map to stable public messages/status', () => {
  assert.equal(publicError(new AgentError('LLM_PROVIDER_UNAVAILABLE', 'raw provider response', 503)).message, 'El asistente está temporalmente fuera de servicio.');
  assert.equal(publicError(Object.assign(new Error('secret'), { name: 'AbortError' })).code, 'AI_SERVICE_TIMEOUT');
  assert.equal(publicError(Object.assign(new Error('redis'), { name: 'MaxRetriesPerRequestError' })).code, 'REDIS_UNAVAILABLE');
  assert.equal(publicError(new AgentError('AI_SCOPE_FORBIDDEN', 'internal details', 403)).status, 403);
});

test('validation errors keep their safe field message and details', () => {
  const error = publicError(new AgentError('AI_INVALID_REQUEST', 'Invalid date: starts_at', 400, false, {
    publicMessage: 'Indica la hora de inicio de la feria.',
    details: { field: 'starts_at' },
  }));
  const body = errorResponse(error, 'req-400');
  assert.equal(body.error.message, 'Indica la hora de inicio de la feria.');
  assert.deepEqual(body.error.details, { field: 'starts_at' });
  assert.equal(JSON.stringify(body).includes('Invalid date'), false);
});

test('invalid LLM responses keep their exact reason in details and public copy', () => {
  const error = publicError(new AgentError('AI_RESPONSE_INVALID', 'LLM returned invalid tool arguments', 502, false, {
    details: { reason: 'INVALID_TOOL_ARGUMENTS_JSON', failure: 'json_decode_error', tool: 'setup_category_with_rubric' },
  }));
  const body = errorResponse(error, 'req-502');
  assert.equal(body.error.code, 'AI_RESPONSE_INVALID');
  assert.equal(body.error.message, 'El asistente no pudo procesar una respuesta válida. Inténtalo nuevamente.');
  assert.deepEqual(body.error.details, {
    reason: 'INVALID_TOOL_ARGUMENTS_JSON',
    failure: 'json_decode_error',
    tool: 'setup_category_with_rubric',
  });
});
