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
