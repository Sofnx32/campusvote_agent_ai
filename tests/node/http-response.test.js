import test from 'node:test';
import assert from 'node:assert/strict';
import { readJsonResponse } from '../../src/node/http-response.js';

const response = (contentType, json) => ({
  headers: new Headers({ 'content-type': contentType }),
  json,
});
const textResponse = (contentType, text) => ({ headers: new Headers({ 'content-type': contentType }), text: async () => text });

test('readJsonResponse accepts JSON and JSON subtypes', async () => {
  assert.deepEqual(await readJsonResponse(response('application/json', async () => ({ success: true })), 'python'), { success: true });
  assert.deepEqual(await readJsonResponse(response('application/problem+json', async () => ({ error: true })), 'campusvote'), { error: true });
});

test('readJsonResponse maps HTML, empty and malformed bodies to controlled errors', async () => {
  await assert.rejects(readJsonResponse(response('text/html', async () => '<!doctype html>'), 'python'), { code: 'AI_SERVICE_UNAVAILABLE', status: 502 });
  await assert.rejects(readJsonResponse(response('text/html', async () => '<!doctype html>'), 'campusvote'), { code: 'AI_BACKEND_UNAVAILABLE', status: 502 });
  await assert.rejects(readJsonResponse(textResponse('application/json', ''), 'python'), { code: 'AI_SERVICE_EMPTY_RESPONSE', status: 502 });
  await assert.rejects(readJsonResponse(textResponse('application/json', '{bad json'), 'campusvote'), { code: 'AI_BACKEND_INVALID_RESPONSE', status: 502 });
  await assert.rejects(readJsonResponse(response('application/json', async () => { throw new SyntaxError('Unexpected token <'); }), 'python'), { code: 'AI_SERVICE_INVALID_RESPONSE', status: 502 });
});
