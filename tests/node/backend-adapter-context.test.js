import test from 'node:test';
import assert from 'node:assert/strict';

process.env.NODE_ENV = 'test';
process.env.CAMPUSVOTE_BACKEND_URL = 'https://backend.test';
process.env.CAMPUSVOTE_RESOLVE_CONTEXT_PATH = '/api/internal/ai/site/context';

const { BackendAdapter } = await import('../../src/node/backend-adapter.js');

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'content-type': 'application/json' },
});

test('resolves the actor context from CampusVote instead of JWT claims', async () => {
  let requested;
  const adapter = new BackendAdapter(async (url, options) => {
    requested = { url: new URL(url), options };
    return jsonResponse({
      success: true,
      data: {
        user_id: 'user-1',
        role: 'ADMIN',
        scope_level: 'SITE',
        organization_id: 'org-1',
        site_ids: ['site-1', 'site-2'],
      },
    });
  });

  const actor = await adapter.resolveActorContext('user-token', new AbortController().signal);

  assert.equal(requested.url.origin, 'https://backend.test');
  assert.equal(requested.url.pathname, '/api/internal/ai/site/context');
  assert.equal(requested.options.headers.authorization, 'Bearer user-token');
  assert.deepEqual(actor, {
    userId: 'user-1',
    role: 'ADMIN',
    scopeLevel: 'SITE',
    organizationId: 'org-1',
    siteIds: ['site-1', 'site-2'],
  });
});

for (const { status, code, expected } of [
  { status: 401, code: 'INVALID_TOKEN', expected: 'AI_SERVICE_UNAUTHORIZED' },
  { status: 403, code: 'AI_ORGANIZATION_REQUIRED', expected: 'AI_ORGANIZATION_REQUIRED' },
  { status: 403, code: 'AI_SITE_ASSIGNMENT_REQUIRED', expected: 'AI_SITE_ASSIGNMENT_REQUIRED' },
  { status: 403, code: 'FORBIDDEN', expected: 'AI_SCOPE_FORBIDDEN' },
  { status: 500, code: 'INTERNAL_SERVER_ERROR', expected: 'AI_TOOL_UNAVAILABLE' },
]) {
  test(`maps ${status}/${code} to ${expected}`, async () => {
    const adapter = new BackendAdapter(async () => jsonResponse({
      success: false,
      error: { code, message: 'backend rejected the context' },
    }, status));

    await assert.rejects(
      () => adapter.resolveActorContext('user-token', new AbortController().signal),
      (error) => error.code === expected
    );
  });
}
