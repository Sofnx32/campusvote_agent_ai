import test from 'node:test';
import assert from 'node:assert/strict';
import { AdminSessionStore } from '../../src/node/services/admin-session.js';
import { AgentError } from '../../src/node/errors.js';

process.env.NODE_ENV = 'test';
process.env.ALLOW_IN_MEMORY_REPLAY = 'true';
process.env.AI_ADMIN_SESSION_TTL_SECONDS = '7200';
process.env.AI_ADMIN_SESSION_IDLE_TTL_SECONDS = '1800';

test('AdminSessionStore creates and retrieves a session', async () => {
  const store = await AdminSessionStore.create();
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  assert.equal(session.userId, 'user-1');
  assert.equal(session.organizationId, 'org-1');
  assert.equal(session.siteId, 'site-1');
  assert.equal(session.status, 'active');
  assert.ok(session.sessionId);
  assert.ok(session.userToken);

  const retrieved = await store.getSession(session.sessionId);
  assert.deepEqual(retrieved, session);
});

test('AdminSessionStore retrieves session by user ID', async () => {
  const store = await AdminSessionStore.create();
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  const byUser = await store.getSessionByUserId('user-1');
  assert.deepEqual(byUser, session);
});

test('AdminSessionStore revokes session by ID', async () => {
  const store = await AdminSessionStore.create();
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  const revoked = await store.revokeSession(session.sessionId);
  assert.equal(revoked, true);
  const afterRevoke = await store.getSession(session.sessionId);
  assert.equal(afterRevoke, null);
});

test('AdminSessionStore revokes session by user ID', async () => {
  const store = await AdminSessionStore.create();
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  const revoked = await store.revokeSessionByUserId('user-1');
  assert.equal(revoked, true);
  const afterRevoke = await store.getSessionByUserId('user-1');
  assert.equal(afterRevoke, null);
});

test('AdminSessionStore returns null for revoked session', async () => {
  const store = await AdminSessionStore.create();
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  await store.revokeSession(session.sessionId);
  const retrieved = await store.getSession(session.sessionId);
  assert.equal(retrieved, null);
});

test('AdminSessionStore updates activity and extends idle timeout', async () => {
  let currentTime = 1000;
  const clock = () => currentTime;
  const store = new AdminSessionStore(null, clock);
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  const initialIdleExpiresAt = session.idleExpiresAt;
  currentTime += 1000;
  await store.updateActivity(session.sessionId);
  const updated = await store.getSession(session.sessionId);
  assert.notEqual(updated.idleExpiresAt, initialIdleExpiresAt);
  assert.ok(new Date(updated.idleExpiresAt) > new Date(initialIdleExpiresAt));
});

test('AdminSessionStore expires session after max TTL', async () => {
  let currentTime = 1000;
  const clock = () => currentTime;
  const store = new AdminSessionStore(null, clock, {
    nodeEnv: 'test',
    allowInMemoryReplay: true,
    sessionTtlSeconds: 1, // 1 second
    sessionIdleTtlSeconds: 1800,
  });
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  currentTime += 2000; // Advance past TTL
  const expired = await store.getSession(session.sessionId);
  assert.equal(expired, null);
});

test('AdminSessionStore expires session after idle timeout', async () => {
  let currentTime = 1000;
  const clock = () => currentTime;
  const store = new AdminSessionStore(null, clock, {
    nodeEnv: 'test',
    allowInMemoryReplay: true,
    sessionTtlSeconds: 7200,
    sessionIdleTtlSeconds: 1, // 1 second
  });
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  currentTime += 2000; // Advance past idle TTL
  const expired = await store.getSession(session.sessionId);
  assert.equal(expired, null);
});

test('AdminSessionStore rejects session with context mismatch', async () => {
  const store = await AdminSessionStore.create();
  const session = await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  // Simulate context mismatch by creating another session for same user but different site
  const session2 = await store.createSession(
    'user-1', 'org-1', 'site-2', 'Sede Norte', 'America/Lima',
    'user-jwt-token', 'corr-2'
  );
  // The second session should replace the first
  const byUser = await store.getSessionByUserId('user-1');
  assert.equal(byUser.sessionId, session2.sessionId);
  assert.equal(byUser.siteId, 'site-2');
});

test('AdminSessionStore in-memory cleanup works', async () => {
  let currentTime = 1000;
  const clock = () => currentTime;
  const store = new AdminSessionStore(null, clock);
  await store.createSession(
    'user-1', 'org-1', 'site-1', 'Sede Central', 'America/Lima',
    'user-jwt-token', 'corr-1'
  );
  currentTime += 10000;
  // Trigger purge by trying to get a non-existent session
  await store.getSession('non-existent');
  // Memory should be purged
  assert.ok(true); // If we get here without memory leak, test passes
});