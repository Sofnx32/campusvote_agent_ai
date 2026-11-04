import test from 'node:test';
import assert from 'node:assert/strict';
import { validateActor } from '../../src/node/context.js';

test('accepts only ADMIN SITE with organization and assigned sites', () => {
  const actor = validateActor({
    sub: 'user-1',
    role: 'ADMIN',
    scopeLevel: 'SITE',
    organizationId: 'org-1',
    siteIds: ['site-1'],
  });
  assert.equal(actor.organizationId, 'org-1');
  assert.deepEqual(actor.siteIds, ['site-1']);
});

for (const claims of [
  { role: 'ADMIN', scopeLevel: 'ORG', organizationId: 'org', siteIds: ['site'] },
  { role: 'ADMIN', scopeLevel: 'REGION', organizationId: 'org', siteIds: ['site'] },
  { role: 'SUPERADMIN', scopeLevel: 'SITE', organizationId: 'org', siteIds: ['site'] },
  { role: 'JURY', scopeLevel: 'SITE', organizationId: 'org', siteIds: ['site'] },
  { role: 'TEACHER', scopeLevel: 'SITE', organizationId: 'org', siteIds: ['site'] },
  { role: 'STUDENT', scopeLevel: 'SITE', organizationId: 'org', siteIds: ['site'] },
]) {
  test(`rejects ${claims.role}/${claims.scopeLevel}`, () => {
    assert.throws(() => validateActor(claims), (error) => error.code === 'AI_SCOPE_FORBIDDEN');
  });
}

test('rejects missing organization or assignments', () => {
  assert.throws(() => validateActor({ role: 'ADMIN', scopeLevel: 'SITE', siteIds: ['site'] }));
  assert.throws(() => validateActor({ role: 'ADMIN', scopeLevel: 'SITE', organizationId: 'org', siteIds: [] }));
});
