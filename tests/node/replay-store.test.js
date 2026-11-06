import test from 'node:test';
import assert from 'node:assert/strict';
import { ReplayStore } from '../../src/node/replay-store.js';

test('atomically consumes once and rejects duplicate tool calls', async () => {
  const store = new ReplayStore();
  await store.setIssued('jti-1', { correlationId: 'corr-1', deadlineAt: new Date(Date.now() + 10000).toISOString() });
  await store.consume('jti-1', { correlationId: 'corr-1' });
  await store.reserveTool('jti-1', 'tool-1', 5);
  await assert.rejects(() => store.reserveTool('jti-1', 'tool-1', 5), (error) => error.code === 'AI_CONTEXT_REPLAYED');
  await assert.rejects(() => store.consume('jti-1', { correlationId: 'corr-1' }), (error) => error.code === 'AI_CONTEXT_REPLAYED');
});

test('rejects a tool call after the request is closed', async () => {
  const store = new ReplayStore();
  await store.setIssued('jti-2', { correlationId: 'corr-2', deadlineAt: new Date(Date.now() + 10000).toISOString() });
  await store.consume('jti-2', { correlationId: 'corr-2' });
  await store.closeRequest('jti-2');
  await assert.rejects(() => store.reserveTool('jti-2', 'tool-1', 5), (error) => error.code === 'AI_CONTEXT_REPLAYED');
});

test('pending action is scoped, replaceable, and consumable only once', async () => {
  const store = new ReplayStore();
  await store.setPendingAction('scope-a', {
    digest: 'digest-a', userId: 'user-a', organizationId: 'org-a', siteId: 'site-a',
  });
  assert.equal((await store.getPendingAction('scope-b')), null);
  assert.equal(await store.consumePendingAction('scope-a', 'wrong-digest'), false);
  assert.equal(await store.consumePendingAction('scope-a', 'digest-a'), true);
  assert.equal(await store.consumePendingAction('scope-a', 'digest-a'), false);
});

test('pending action changes invalidate its prior confirmation digest', async () => {
  const store = new ReplayStore();
  await store.setPendingAction('scope-a', { digest: 'original' });
  await store.setPendingAction('scope-a', { digest: 'modified' });
  assert.equal(await store.consumePendingAction('scope-a', 'original'), false);
  assert.equal(await store.consumePendingAction('scope-a', 'modified'), true);
});

test('pending action expires after its confirmation window', async () => {
  let currentTime = 1000;
  const store = new ReplayStore(null, () => currentTime);
  await store.setPendingAction('scope-expiring', { digest: 'digest' });
  currentTime += 10 * 60 * 1000;
  assert.equal(await store.getPendingAction('scope-expiring'), null);
  assert.equal(await store.consumePendingAction('scope-expiring', 'digest'), false);
});

test('maps Redis outages to a stable retryable REDIS_UNAVAILABLE error', async () => {
  const store = new ReplayStore({ set: async () => { throw Object.assign(new Error('redis password=hidden'), { code: 'ECONNREFUSED' }); } });
  await assert.rejects(() => store.setIssued('jti-redis', { correlationId: 'corr-redis', deadlineAt: new Date(Date.now() + 10000).toISOString() }),
    (error) => error.code === 'REDIS_UNAVAILABLE' && error.status === 503 && error.retryable === true && !error.message.includes('password'));
});
