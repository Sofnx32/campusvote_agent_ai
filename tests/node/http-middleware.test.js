import test from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import { jsonParserErrorHandler } from '../../src/node/http-middleware.js';

test('malformed JSON receives the agent JSON error contract, not Express HTML', async (t) => {
  const app = express();
  app.use((req, _res, next) => { req.requestId = 'req-json-invalid-1'; next(); });
  app.use(express.json());
  app.use(jsonParserErrorHandler);
  app.post('/v1/chat', (_req, res) => res.json({ success: true }));
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, '127.0.0.1', () => resolve(instance));
  });
  t.after(() => server.close());
  const { port } = server.address();
  const response = await fetch(`http://127.0.0.1:${port}/v1/chat`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{bad json',
  });
  const body = await response.json();
  assert.equal(response.status, 400);
  assert.equal(response.headers.get('content-type').includes('application/json'), true);
  assert.equal(response.headers.get('x-request-id'), 'req-json-invalid-1');
  assert.deepEqual(body.error, {
    code: 'VALIDATION_INVALID_JSON', message: 'Revisa la información ingresada.', retryable: false,
    requestId: 'req-json-invalid-1', details: null,
  });
});
