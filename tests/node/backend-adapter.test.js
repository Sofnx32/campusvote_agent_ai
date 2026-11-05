import test from 'node:test';
import assert from 'node:assert/strict';
import { BackendAdapter } from '../../src/node/backend-adapter.js';

test('fails closed when the active assignment verifier is not configured', async () => {
  const adapter = new BackendAdapter();
  await assert.rejects(
    () => adapter.assertActiveAssignment({
      sub: 'user-1',
      organization_id: 'org-1',
      site_id: 'site-1',
    }, 'site-1', 'user-token', new AbortController().signal),
    (error) => error.code === 'AI_TOOL_UNAVAILABLE'
  );
});
