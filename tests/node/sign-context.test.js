import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import jwt from 'jsonwebtoken';

const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
process.env.NODE_ENV = 'test';
process.env.AI_CONTEXT_PRIVATE_KEY = privateKey.export({ type: 'pkcs8', format: 'pem' });
process.env.AI_CONTEXT_PUBLIC_KEY = publicKey.export({ type: 'spki', format: 'pem' });
process.env.AI_CONTEXT_ISSUER = 'issuer';
process.env.AI_CONTEXT_AUDIENCE = 'audience';

const { signContext, verifyContext } = await import('../../src/node/context.js');

test('signContext includes the jti claim required by the Python agent', () => {
  const result = signContext(
    { userId: 'user-1', role: 'ADMIN', scopeLevel: 'SITE', organizationId: 'org-1', siteIds: ['site-1'] },
    'corr-1',
    new Date(Date.now() + 10000).toISOString()
  );
  const decoded = jwt.decode(result.token);
  assert.equal(decoded.jti, result.jti);
  const claims = verifyContext(result.token, { publicKey, issuer: 'issuer', audience: 'audience' });
  assert.equal(claims.jti, result.jti);
  assert.equal(claims.correlation_id, 'corr-1');
});
