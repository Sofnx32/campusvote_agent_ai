import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import jwt from 'jsonwebtoken';
import { verifyContext } from '../../src/node/context.js';

const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const baseClaims = {
  iss: 'issuer',
  aud: 'audience',
  sub: 'user-1',
  role: 'ADMIN',
  scope_level: 'SITE',
  organization_id: 'org-1',
  site_ids: ['site-1'],
  jti: 'jti-1',
  correlation_id: 'corr-1',
  deadline_at: new Date(Date.now() + 10000).toISOString(),
};

test('verifies a valid RS256 context and rejects HS256', () => {
  const valid = jwt.sign(baseClaims, privateKey, { algorithm: 'RS256', expiresIn: 60 });
  assert.equal(verifyContext(valid, { publicKey, issuer: 'issuer', audience: 'audience' }).role, 'ADMIN');
  const insecure = jwt.sign(baseClaims, 'shared-secret', { algorithm: 'HS256', expiresIn: 60 });
  assert.throws(() => verifyContext(insecure, { publicKey, issuer: 'issuer', audience: 'audience' }), (error) => error.code === 'AI_CONTEXT_INVALID');
});

test('rejects a context with invalid audience', () => {
  const token = jwt.sign({ ...baseClaims, aud: 'other' }, privateKey, { algorithm: 'RS256', expiresIn: 60 });
  assert.throws(() => verifyContext(token, { publicKey, issuer: 'issuer', audience: 'audience' }), (error) => error.code === 'AI_CONTEXT_INVALID');
});
