import test from 'node:test';
import assert from 'node:assert/strict';

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'content-type': 'application/json' },
});

test('ActorResolver propagates reason from CampusVote 401 response', async () => {
  process.env.NODE_ENV = 'test';
  process.env.CAMPUSVOTE_BACKEND_URL = 'https://backend.test';
  process.env.CAMPUSVOTE_RESOLVE_CONTEXT_PATH = '/api/internal/ai/site/context';

  const { ActorResolver } = await import('../../src/node/services/actor-resolver.js');

  const adapter = new ActorResolver(async () => jsonResponse({
    success: false,
    error: { code: 'TOKEN_EXPIRED', message: 'Token has expired' },
  }, 401));

  try {
    await adapter.resolve('user-token', new AbortController().signal, 'corr-123');
    assert.fail('expected resolve to throw');
  } catch (error) {
    assert.equal(error.code, 'AI_SERVICE_UNAUTHORIZED');
    assert.equal(error.status, 401);
    assert.deepEqual(error.details, { reason: 'TOKEN_EXPIRED' });
  }
});

test('ActorResolver uses UNKNOWN reason when CampusVote 401 has no error code', async () => {
  process.env.NODE_ENV = 'test';
  process.env.CAMPUSVOTE_BACKEND_URL = 'https://backend.test';
  process.env.CAMPUSVOTE_RESOLVE_CONTEXT_PATH = '/api/internal/ai/site/context';

  const { ActorResolver } = await import('../../src/node/services/actor-resolver.js');

  const adapter = new ActorResolver(async () => jsonResponse({
    success: false,
    error: { message: 'Unauthorized' },
  }, 401));

  try {
    await adapter.resolve('user-token', new AbortController().signal, 'corr-456');
    assert.fail('expected resolve to throw');
  } catch (error) {
    assert.equal(error.code, 'AI_SERVICE_UNAUTHORIZED');
    assert.equal(error.status, 401);
    assert.deepEqual(error.details, { reason: 'UNKNOWN' });
  }
});

test('ActorResolver logs campusvote_auth_failed event on 401', async () => {
  process.env.NODE_ENV = 'test';
  process.env.CAMPUSVOTE_BACKEND_URL = 'https://backend.test';
  process.env.CAMPUSVOTE_RESOLVE_CONTEXT_PATH = '/api/internal/ai/site/context';

  const { ActorResolver } = await import('../../src/node/services/actor-resolver.js');

  let logged = false;
  const originalWarn = console.warn;
  console.warn = (msg) => {
    try {
      const parsed = JSON.parse(msg);
      if (parsed.event === 'campusvote_auth_failed' && parsed.reason === 'TOKEN_EXPIRED') {
        logged = true;
      }
    } catch {}
    originalWarn(msg);
  };

  const adapter = new ActorResolver(async () => jsonResponse({
    success: false,
    error: { code: 'TOKEN_EXPIRED', message: 'Token has expired' },
  }, 401));

  try {
    await adapter.resolve('user-token', new AbortController().signal, 'corr-789');
  } catch {}

  console.warn = originalWarn;
  assert.ok(logged, 'expected campusvote_auth_failed event to be logged');
});