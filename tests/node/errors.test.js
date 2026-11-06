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
