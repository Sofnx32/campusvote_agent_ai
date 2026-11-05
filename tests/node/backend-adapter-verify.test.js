import test from 'node:test';
import assert from 'node:assert/strict';

process.env.NODE_ENV = 'test';
process.env.CAMPUSVOTE_BACKEND_URL = 'https://backend.test';
process.env.CAMPUSVOTE_VERIFY_SITE_PATH = '/api/internal/ai/site/verify';
process.env.CAMPUSVOTE_VERIFY_FAIR_PATH = '/api/fairs/:fairId';

const { BackendAdapter } = await import('../../src/node/backend-adapter.js');

test('sends site_id when verifying the active assignment', async () => {
  let requested;
  const adapter = new BackendAdapter(async (url, options) => {
    requested = { url: new URL(url), options };
    return new Response(JSON.stringify({
      success: true,
      data: { active: true, organization_id: 'org-1', site_id: 'site-1' },
    }), { status: 200, headers: { 'content-type': 'application/json' } });
  });

  await adapter.assertActiveAssignment(
    { sub: 'user-1', organization_id: 'org-1', site_ids: ['site-1'] },
    'site-1',
    'user-token',
    new AbortController().signal
  );

  assert.equal(requested.url.origin, 'https://backend.test');
  assert.equal(requested.url.pathname, '/api/internal/ai/site/verify');
  assert.equal(requested.url.searchParams.get('site_id'), 'site-1');
  assert.equal(requested.options.headers.authorization, 'Bearer user-token');
});

test('rejects a revoked or expired site assignment', async () => {
  const adapter = new BackendAdapter(async () => new Response(JSON.stringify({
    success: false,
    error: { code: 'FORBIDDEN', message: 'La sede no estǭ asignada activamente' },
  }), { status: 403, headers: { 'content-type': 'application/json' } }));

  await assert.rejects(
    () => adapter.assertActiveAssignment(
      { sub: 'user-1', organization_id: 'org-1', site_ids: ['site-1'] },
      'site-1',
      'user-token',
      new AbortController().signal
    ),
    (error) => error.code === 'AI_SCOPE_FORBIDDEN'
  );
});

test('rejects a site verifier response scoped to another organization', async () => {
  const adapter = new BackendAdapter(async () => new Response(JSON.stringify({
    success: true,
    data: { active: true, organization_id: 'org-2', site_id: 'site-1' },
  }), { status: 200, headers: { 'content-type': 'application/json' } }));

  await assert.rejects(
    () => adapter.assertActiveAssignment(
      { sub: 'user-1', organization_id: 'org-1', site_ids: ['site-1'] },
      'site-1',
      'user-token',
      new AbortController().signal
    ),
    (error) => error.code === 'AI_SCOPE_FORBIDDEN'
  );
});

test('accepts fair scope when the backend exposes the site as site.id', async () => {
  const adapter = new BackendAdapter(async () => new Response(JSON.stringify({
    success: true,
    data: {
      id: 'fair-1',
      organization_id: 'org-1',
      site: { id: 'site-1', name: 'Sede 1' },
    },
  }), { status: 200, headers: { 'content-type': 'application/json' } }));

  await adapter.assertFairAssignment(
    'fair-1',
    'site-1',
    { sub: 'user-1', organization_id: 'org-1', site_ids: ['site-1'] },
    'user-token',
    new AbortController().signal
  );
});
