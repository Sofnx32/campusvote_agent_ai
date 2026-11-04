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

export const validateActor = (claims) => {
  if (!allowedRoles.has(claims.role) || claims.scopeLevel !== 'SITE' && claims.scope_level !== 'SITE') {
    throw new AgentError('AI_SCOPE_FORBIDDEN', 'Only ADMIN SITE is allowed', 403);
  }
  const organizationId = claims.organizationId || claims.organization_id;
  const siteIds = claims.siteIds || claims.site_ids;
  if (!organizationId || !Array.isArray(siteIds) || siteIds.length === 0) {
    throw new AgentError('AI_CONTEXT_INVALID', 'organizationId and active site assignments are required', 401);
  }
  return { userId: claims.sub || claims.userId, role: 'ADMIN', scopeLevel: 'SITE', organizationId, siteIds };
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
      role: actor.role,
      scope_level: actor.scopeLevel,
      organization_id: actor.organizationId,
      site_ids: actor.siteIds,
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
  if (claims.role !== 'ADMIN' || claims.scope_level !== 'SITE' || !claims.organization_id || !Array.isArray(claims.site_ids) || !claims.jti || !claims.correlation_id) {
    throw new AgentError('AI_CONTEXT_INVALID', 'Incomplete AI context', 401);
  }
  if (Date.parse(claims.deadline_at) <= Date.now()) throw new AgentError('AI_DEADLINE_EXCEEDED', 'Request deadline exceeded', 504, true);
  return claims;
};
