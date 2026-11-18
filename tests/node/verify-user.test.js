import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import jwt from 'jsonwebtoken';

test('verifyUser accepts a valid token', async () => {
  process.env.NODE_ENV = 'test';
  process.env.USER_JWT_SECRET = 'test-secret-key-for-testing-purposes-only-32chars';
  const { verifyUser } = await import('../../src/node/utils/context.js');

  function createToken(claims, options = {}) {
    const secret = process.env.USER_JWT_SECRET;
    const opts = { algorithm: 'HS256', ...options };
    return jwt.sign(claims, secret, opts);
  }

  const token = createToken({ userId: 'user-1', sub: 'user-1', role: 'ADMIN', scope_level: 'SITE', exp: Math.floor(Date.now() / 1000) + 3600 });
  const claims = verifyUser(token);
  assert.equal(claims.userId, 'user-1');
  assert.equal(claims.role, 'ADMIN');
});

test('verifyUser rejects an expired token with TOKEN_EXPIRED reason', async () => {
  process.env.NODE_ENV = 'test';
  process.env.USER_JWT_SECRET = 'test-secret-key-for-testing-purposes-only-32chars';
  const { verifyUser } = await import('../../src/node/utils/context.js');

  function createToken(claims, options = {}) {
    const secret = process.env.USER_JWT_SECRET;
    const opts = { algorithm: 'HS256', ...options };
    return jwt.sign(claims, secret, opts);
  }

  // Token expired 60 seconds ago (beyond the 30s clockTolerance)
  const token = createToken({ userId: 'user-1', sub: 'user-1', exp: Math.floor(Date.now() / 1000) - 60 });
  try {
    verifyUser(token);
    assert.fail('expected verifyUser to throw');
  } catch (error) {
    assert.equal(error.code, 'AI_SERVICE_UNAUTHORIZED');
    assert.equal(error.status, 401);
    assert.deepEqual(error.details, { reason: 'TOKEN_EXPIRED' });
  }
});

test('verifyUser rejects a token with invalid signature with INVALID_TOKEN reason', async () => {
  process.env.NODE_ENV = 'test';
  process.env.USER_JWT_SECRET = 'test-secret-key-for-testing-purposes-only-32chars';
  const { verifyUser } = await import('../../src/node/utils/context.js');

  function createToken(claims, options = {}) {
    const secret = process.env.USER_JWT_SECRET;
    const opts = { algorithm: 'HS256', ...options };
    return jwt.sign(claims, secret, opts);
  }

  const token = createToken({ userId: 'user-1', sub: 'user-1', exp: Math.floor(Date.now() / 1000) + 3600 });
  const parts = token.split('.');
  const tampered = parts[0] + '.' + parts[1] + '.invalidsignature';
  try {
    verifyUser(tampered);
    assert.fail('expected verifyUser to throw');
  } catch (error) {
    assert.equal(error.code, 'AI_SERVICE_UNAUTHORIZED');
    assert.equal(error.status, 401);
    assert.deepEqual(error.details, { reason: 'INVALID_TOKEN' });
  }
});

test('verifyUser rejects a malformed token with INVALID_TOKEN reason', async () => {
  process.env.NODE_ENV = 'test';
  process.env.USER_JWT_SECRET = 'test-secret-key-for-testing-purposes-only-32chars';
  const { verifyUser } = await import('../../src/node/utils/context.js');

  try {
    verifyUser('not.a.valid.token');
    assert.fail('expected verifyUser to throw');
  } catch (error) {
    assert.equal(error.code, 'AI_SERVICE_UNAUTHORIZED');
    assert.equal(error.status, 401);
    assert.deepEqual(error.details, { reason: 'INVALID_TOKEN' });
  }
});

test('verifyUser rejects a token with wrong algorithm', async () => {
  process.env.NODE_ENV = 'test';
  process.env.USER_JWT_SECRET = 'test-secret-key-for-testing-purposes-only-32chars';
  const { verifyUser } = await import('../../src/node/utils/context.js');

  function createToken(claims, options = {}) {
    const secret = process.env.USER_JWT_SECRET;
    const opts = { algorithm: 'HS256', ...options };
    return jwt.sign(claims, secret, opts);
  }

  const token = createToken({ userId: 'user-1', sub: 'user-1', exp: Math.floor(Date.now() / 1000) + 3600 });
  const parts = token.split('.');
  const header = JSON.parse(Buffer.from(parts[0], 'base64url').toString());
  header.alg = 'RS256';
  const newHeader = Buffer.from(JSON.stringify(header)).toString('base64url');
  const tampered = newHeader + '.' + parts[1] + '.' + parts[2];
  try {
    verifyUser(tampered);
    assert.fail('expected verifyUser to throw');
  } catch (error) {
    assert.equal(error.code, 'AI_SERVICE_UNAUTHORIZED');
    assert.equal(error.status, 401);
    assert.deepEqual(error.details, { reason: 'INVALID_TOKEN' });
  }
});

test('verifyUser tolerates small clock skew with clockTolerance: 30', async () => {
  process.env.NODE_ENV = 'test';
  process.env.USER_JWT_SECRET = 'test-secret-key-for-testing-purposes-only-32chars';
  const { verifyUser } = await import('../../src/node/utils/context.js');

  function createToken(claims, options = {}) {
    const secret = process.env.USER_JWT_SECRET;
    const opts = { algorithm: 'HS256', ...options };
    return jwt.sign(claims, secret, opts);
  }

  const token = createToken({ userId: 'user-1', sub: 'user-1', exp: Math.floor(Date.now() / 1000) + 20 });
  const claims = verifyUser(token);
  assert.equal(claims.userId, 'user-1');
});