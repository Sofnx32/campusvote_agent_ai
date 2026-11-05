import test from 'node:test';
import assert from 'node:assert/strict';
import { validateActor } from '../../src/node/context.js';

test('accepts an authoritative ADMIN SITE context resolved by CampusVote', () => {
  const actor = validateActor({
    user_id: 'user-1',
    role: 'ADMIN',
    scope_level: 'SITE',
    organization_id: 'org-1',
    site_ids: ['site-1', 'site-1', 'site-2'],
  });
  assert.equal(actor.userId, 'user-1');
  assert.equal(actor.organizationId, 'org-1');
  assert.deepEqual(actor.siteIds, ['site-1', 'site-2']);
});

test('accepts camelCase context for local adapters', () => {
  const actor = validateActor({
    userId: 'user-1',
    role: 'ADMIN',
    scopeLevel: 'SITE',
    organizationId: 'org-1',
    siteIds: ['site-1'],
  });
  assert.equal(actor.userId, 'user-1');
  assert.deepEqual(actor.siteIds, ['site-1']);
});

for (const actor of [
  { role: 'ADMIN', scope_level: 'ORG', organization_id: 'org', site_ids: ['site'] },
  { role: 'ADMIN', scope_level: 'REGION', organization_id: 'org', site_ids: ['site'] },
  { role: 'SUPERADMIN', scope_level: 'SITE', organization_id: 'org', site_ids: ['site'] },
  { role: 'JURY', scope_level: 'SITE', organization_id: 'org', site_ids: ['site'] },
  { role: 'TEACHER', scope_level: 'SITE', organization_id: 'org', site_ids: ['site'] },
  { role: 'STUDENT', scope_level: 'SITE', organization_id: 'org', site_ids: ['site'] },
]) {
  test(`rejects ${actor.role}/${actor.scope_level}`, () => {
    assert.throws(() => validateActor(actor), (error) => error.code === 'AI_SCOPE_FORBIDDEN');
  });
}

test('rejects an ADMIN SITE without an organization', () => {
  assert.throws(
    () => validateActor({ user_id: 'user-1', role: 'ADMIN', scope_level: 'SITE', site_ids: ['site'] }),
    (error) => error.code === 'AI_ORGANIZATION_REQUIRED'
  );
});

test('rejects an ADMIN SITE without active assignments', () => {
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
      site_ids: ['site'],
    }),
    (error) => error.code === 'AI_CONTEXT_INVALID'
  );
});
