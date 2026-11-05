import crypto from 'node:crypto';
import jwt from 'jsonwebtoken';
import { config } from './config.js';
import { AgentError } from './errors.js';

const allowedRoles = new Set(['ADMIN']);

export const extractBearer = (header) => {
  if (!header?.startsWith('Bearer ')) throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'Bearer token required', 401);
  return header.slice(7);
};

export const verifyUser = (token) => {
  const key = config.userJwtPublicKey || config.userJwtSecret;
  const algorithms = config.userJwtPublicKey ? ['RS256'] : ['HS256'];
  if (!key) throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'User JWT verification key is not configured', 401);
  try {
    return jwt.verify(token, key, { algorithms });
  } catch {
    throw new AgentError('AI_SERVICE_UNAUTHORIZED', 'Invalid user token', 401);
  }
};

export const validateActor = (actor) => {
  const role = actor?.role;
  const scopeLevel = actor?.scopeLevel ?? actor?.scope_level;
  if (!allowedRoles.has(role) || scopeLevel !== 'SITE') {
    throw new AgentError('AI_SCOPE_FORBIDDEN', 'Only ADMIN SITE is allowed', 403);
  }
  const userId = actor.userId ?? actor.user_id ?? actor.id ?? actor.sub;
  if (!userId) {
    throw new AgentError('AI_CONTEXT_INVALID', 'Authenticated administrator could not be identified', 401);
  }
  const organizationId = actor.organizationId ?? actor.organization_id;
  if (!organizationId) {
    throw new AgentError('AI_ORGANIZATION_REQUIRED', 'The administrator does not belong to an organization', 403);
  }
  const rawSiteId = actor.siteId ?? actor.site_id;
  const rawSiteIds = actor.siteIds ?? actor.site_ids;
  const siteId = typeof rawSiteId === 'string'
    ? rawSiteId
    : Array.isArray(rawSiteIds) && rawSiteIds.length === 1
      ? rawSiteIds[0]
      : null;
  if (!siteId || typeof siteId !== 'string') {
    if (Array.isArray(rawSiteIds) && rawSiteIds.length === 0) {
      throw new AgentError('AI_SITE_ASSIGNMENT_REQUIRED', 'The administrator has no active site assignment', 403);
    }
    if (Array.isArray(rawSiteIds) && rawSiteIds.length > 1) {
      throw new AgentError('AI_SCOPE_FORBIDDEN', 'The administrator has more than one active site assignment', 403);
    }
    throw new AgentError('AI_SITE_ASSIGNMENT_REQUIRED', 'The administrator has no active site assignment', 403);
  }
  return {
    userId,
    role: 'ADMIN',
    scopeLevel: 'SITE',
    organizationId,
    siteId,
    siteName: typeof actor.siteName === 'string'
      ? actor.siteName.replace(/\s+/g, ' ').trim()
      : typeof actor.site_name === 'string'
        ? actor.site_name.replace(/\s+/g, ' ').trim()
        : undefined,
  };
};

export const signContext = (actor, correlationId, deadlineAt) => {
  if (!config.contextPrivateKey) throw new AgentError('AI_CONTEXT_INVALID', 'Context signing key is not configured', 500);
  const jti = crypto.randomUUID();
  const nowSeconds = Math.floor(Date.now() / 1000);
  return {
    jti,
    token: jwt.sign({
      iss: config.issuer,
      aud: config.audience,
      sub: actor.userId,
      jti,
      role: actor.role,
      scope_level: actor.scopeLevel,
      organization_id: actor.organizationId,
      site_id: actor.siteId,
      site_name: actor.siteName,
      correlation_id: correlationId,
      deadline_at: deadlineAt,
      iat: nowSeconds,
    }, config.contextPrivateKey, {
      algorithm: 'RS256',
      keyid: config.contextKeyId,
      expiresIn: config.contextTtlSeconds,
    }),
    claims: { ...actor, jti, correlation_id: correlationId, deadline_at: deadlineAt },
  };
};

export const verifyContext = (token, options = {}) => {
  const publicKey = options.publicKey || config.contextPublicKey;
  if (!publicKey) throw new AgentError('AI_CONTEXT_INVALID', 'Context verification key is not configured', 401);
  let claims;
  try {
    claims = jwt.verify(token, publicKey, {
      algorithms: ['RS256'],
      issuer: options.issuer || config.issuer,
      audience: options.audience || config.audience,
      complete: false,
    });
  } catch {
    throw new AgentError('AI_CONTEXT_INVALID', 'Invalid AI context', 401);
  }
  if (claims.role !== 'ADMIN' || claims.scope_level !== 'SITE' || !claims.organization_id || typeof claims.site_id !== 'string' || !claims.jti || !claims.correlation_id) {
    throw new AgentError('AI_CONTEXT_INVALID', 'Incomplete AI context', 401);
  }
  if (Date.parse(claims.deadline_at) <= Date.now()) throw new AgentError('AI_DEADLINE_EXCEEDED', 'Request deadline exceeded', 504, true);
  return claims;
};
