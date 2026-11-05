import test from 'node:test';
import assert from 'node:assert/strict';

process.env.NODE_ENV = 'test';
process.env.CAMPUSVOTE_BACKEND_URL = 'https://backend.test';
process.env.TOOL_CREATE_FAIR_PATH = '/api/fairs';
process.env.TOOL_CREATE_FAIR_METHOD = 'POST';

const { BackendAdapter } = await import('../../src/node/backend-adapter.js');

test('creates a fair through the authenticated CampusVote endpoint with scoped site', async () => {
  let request;
  const adapter = new BackendAdapter(async (url, options) => {
    request = { url: new URL(url), options };
    return new Response(JSON.stringify({ success: true, data: { id: 'fair-1', name: 'Feria' } }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    });
  });

  const result = await adapter.callTool('createFair', {
    name: 'Feria',
    image_url: 'https://cdn.example/cover.png',
    starts_at: '2027-05-01T09:00:00Z',
    site_id: 'site-authorized',
  }, {
    sub: 'user-1',
    organization_id: 'org-authorized',
    site_id: 'site-authorized',
  }, 'user-jwt');

  assert.equal(request.url.pathname, '/api/fairs');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers.authorization, 'Bearer user-jwt');
  assert.deepEqual(JSON.parse(request.options.body), {
    name: 'Feria',
    image_url: 'https://cdn.example/cover.png',
    starts_at: '2027-05-01T09:00:00Z',
    site_id: 'site-authorized',
  });
  assert.deepEqual(result, { id: 'fair-1', name: 'Feria' });
});

test('does not hide CampusVote create errors as successful fair results', async () => {
  const adapter = new BackendAdapter(async () => new Response('{}', { status: 403 }));
  await assert.rejects(
    () => adapter.callTool('createFair', { name: 'Feria', site_id: 'site-a' }, {
      sub: 'user-1', organization_id: 'org-a', site_id: 'site-a',
    }, 'user-jwt'),
    (error) => error.code === 'AI_SCOPE_FORBIDDEN'
  );
});

test('creates a SITE teaching criterion using only the signed site context', async () => {
  let request;
  const adapter = new BackendAdapter(async (url, options) => {
    request = { url: new URL(url), options };
    return new Response(JSON.stringify({ success: true, data: { id: 'criterion-a' } }), { status: 201 });
  });
  await adapter.callTool('createTeachingCriterion', {
    name: 'Claridad docente', site_id: 'site-authorized',
  }, { sub: 'user-1', organization_id: 'org-authorized', site_id: 'site-authorized' }, 'user-jwt');
  assert.equal(request.url.pathname, '/api/academic/evaluation-criteria');
  assert.deepEqual(JSON.parse(request.options.body), { name: 'Claridad docente', siteId: 'site-authorized' });
  assert.equal(request.options.headers.authorization, 'Bearer user-jwt');
});
