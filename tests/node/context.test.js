import test from 'node:test';
import assert from 'node:assert/strict';
import { validateActor } from '../../src/node/context.js';

test('accepts an authoritative single-site ADMIN SITE context resolved by CampusVote', () => {
  const actor = validateActor({
    user_id: 'user-1',
    role: 'ADMIN',
    scope_level: 'SITE',
    organization_id: 'org-1',
    site_id: 'site-1',
    site_name: 'Sede Central',
  });
  assert.equal(actor.userId, 'user-1');
  assert.equal(actor.organizationId, 'org-1');
  assert.equal(actor.siteId, 'site-1');
  assert.equal(actor.siteName, 'Sede Central');
});

test('accepts camelCase context for local adapters', () => {
  const actor = validateActor({
    userId: 'user-1',
    role: 'ADMIN',
    scopeLevel: 'SITE',
    organizationId: 'org-1',
    siteId: 'site-1',
  });
  assert.equal(actor.userId, 'user-1');
  assert.equal(actor.siteId, 'site-1');
});

test('rejects legacy accounts with more than one active site', () => {
  assert.throws(
    () => validateActor({
      user_id: 'user-1',
      role: 'ADMIN',
      scope_level: 'SITE',
      organization_id: 'org-1',
      site_ids: ['site-1', 'site-2'],
    }),
    (error) => error.code === 'AI_SCOPE_FORBIDDEN'
  );
});

for (const actor of [
  { role: 'ADMIN', scope_level: 'ORG', organization_id: 'org', site_id: 'site' },
  { role: 'ADMIN', scope_level: 'REGION', organization_id: 'org', site_id: 'site' },
  { role: 'SUPERADMIN', scope_level: 'SITE', organization_id: 'org', site_id: 'site' },
  { role: 'JURY', scope_level: 'SITE', organization_id: 'org', site_id: 'site' },
  { role: 'TEACHER', scope_level: 'SITE', organization_id: 'org', site_id: 'site' },
  { role: 'STUDENT', scope_level: 'SITE', organization_id: 'org', site_id: 'site' },
]) {
  test(`rejects ${actor.role}/${actor.scope_level}`, () => {
    assert.throws(() => validateActor(actor), (error) => error.code === 'AI_SCOPE_FORBIDDEN');
  });
}

test('rejects an ADMIN SITE without an organization', () => {
  assert.throws(
    () => validateActor({ user_id: 'user-1', role: 'ADMIN', scope_level: 'SITE', site_id: 'site' }),
    (error) => error.code === 'AI_ORGANIZATION_REQUIRED'
  );
});

test('rejects an ADMIN SITE without an active site', () => {
  assert.throws(
    () => validateActor({
      user_id: 'user-1',
      role: 'ADMIN',
      scope_level: 'SITE',
      organization_id: 'org',
      site_ids: [],
    }),
    (error) => error.code === 'AI_SITE_ASSIGNMENT_REQUIRED'
  );
});

test('rejects a context that cannot identify the administrator', () => {
  assert.throws(
    () => validateActor({
      role: 'ADMIN',
      scope_level: 'SITE',
      organization_id: 'org',
      site_id: 'site',
    }),
    (error) => error.code === 'AI_CONTEXT_INVALID'
  );
});
