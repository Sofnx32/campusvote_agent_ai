import fs from 'node:fs';

const required = (name, value) => {
  if (!value) throw new Error(`Missing required configuration: ${name}`);
  return value;
};

const readKey = (path, name, requiredInProduction = true) => {
  if (!path) {
    if (requiredInProduction && process.env.NODE_ENV === 'production') {
      throw new Error(`Missing required configuration: ${name}_PATH`);
    }
    return null;
  }
  return fs.readFileSync(path, 'utf8');
};

export const config = Object.freeze({
  nodeEnv: process.env.NODE_ENV || 'development',
  port: Number(process.env.PORT || 3000),
  allowedOrigins: (process.env.FRONTEND_ORIGINS || 'http://localhost:5173')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean),
  pythonUrl: required('PYTHON_SERVICE_URL', process.env.PYTHON_SERVICE_URL || 'http://127.0.0.1:8000'),
  serviceToken: required('AI_SERVICE_TOKEN', process.env.AI_SERVICE_TOKEN || 'development-token-change-me'),
  mtlsRequired: process.env.AI_MTLS_REQUIRED === 'true',
  mtlsCaPath: process.env.AI_MTLS_CA_PATH || '',
  mtlsCertPath: process.env.AI_MTLS_CERT_PATH || '',
  mtlsKeyPath: process.env.AI_MTLS_KEY_PATH || '',
  userJwtPublicKey: readKey(process.env.USER_JWT_PUBLIC_KEY_PATH, 'USER_JWT_PUBLIC_KEY', false),
  userJwtSecret: readKey(process.env.USER_JWT_SECRET_PATH, 'USER_JWT_SECRET', false),
  contextPrivateKey: readKey(process.env.AI_CONTEXT_PRIVATE_KEY_PATH, 'AI_CONTEXT_PRIVATE_KEY'),
  contextPublicKey: readKey(process.env.AI_CONTEXT_PUBLIC_KEY_PATH, 'AI_CONTEXT_PUBLIC_KEY'),
  contextKeyId: process.env.AI_CONTEXT_KEY_ID || 'ai-context-development',
  issuer: process.env.AI_CONTEXT_ISSUER || 'campusvote-api',
  audience: process.env.AI_CONTEXT_AUDIENCE || 'campusvote-agent-ai',
  contextTtlSeconds: Number(process.env.AI_CONTEXT_TTL_SECONDS || 60),
  jtiTtlSeconds: Number(process.env.AI_JTI_STATE_TTL_SECONDS || 90),
  chatTimeoutMs: Number(process.env.AI_CHAT_TIMEOUT_MS || 25000),
  toolTimeoutMs: Number(process.env.AI_TOOL_TIMEOUT_MS || 5000),
  maxToolCalls: Number(process.env.AI_MAX_TOOL_CALLS || 5),
  maxToolBytes: Number(process.env.AI_TOOL_AGGREGATE_MAX_BYTES || 524288),
  redisUrl: process.env.REDIS_URL || '',
  allowInMemoryReplay: process.env.ALLOW_IN_MEMORY_REPLAY === 'true',
  backendUrl: process.env.CAMPUSVOTE_BACKEND_URL || '',
  backendAuthMode: process.env.CAMPUSVOTE_AUTH_MODE || 'user_jwt',
  requireScopedToolEnvelope: process.env.CAMPUSVOTE_TOOL_RESPONSE_ENVELOPE_REQUIRED !== 'false',
  verifySitePath: process.env.CAMPUSVOTE_VERIFY_SITE_PATH || '',
  verifyFairPath: process.env.CAMPUSVOTE_VERIFY_FAIR_PATH || '',
  toolPaths: {
    getSiteOverview: process.env.TOOL_GET_SITE_OVERVIEW_PATH || '',
    getSiteFairs: process.env.TOOL_GET_SITE_FAIRS_PATH || '',
    getSiteProjects: process.env.TOOL_GET_SITE_PROJECTS_PATH || '',
    getSiteStands: process.env.TOOL_GET_SITE_STANDS_PATH || '',
    getSiteResults: process.env.TOOL_GET_SITE_RESULTS_PATH || '',
    createFairRubric: process.env.TOOL_CREATE_FAIR_RUBRIC_PATH || '',
    addFairRubricCriterion: process.env.TOOL_ADD_FAIR_RUBRIC_CRITERION_PATH || '',
    createFair: process.env.TOOL_CREATE_FAIR_PATH || '',
    updateFair: process.env.TOOL_UPDATE_FAIR_PATH || '',
    changeFairStatus: process.env.TOOL_CHANGE_FAIR_STATUS_PATH || '',
    listFairStands: process.env.TOOL_LIST_FAIR_STANDS_PATH || '',
    createFairStand: process.env.TOOL_CREATE_FAIR_STAND_PATH || '',
    updateFairStand: process.env.TOOL_UPDATE_FAIR_STAND_PATH || '',
    deleteFairStand: process.env.TOOL_DELETE_FAIR_STAND_PATH || '',
  },
  toolMethods: {
    getSiteOverview: process.env.TOOL_GET_SITE_OVERVIEW_METHOD || 'GET',
    getSiteFairs: process.env.TOOL_GET_SITE_FAIRS_METHOD || 'GET',
    getSiteProjects: process.env.TOOL_GET_SITE_PROJECTS_METHOD || 'GET',
    getSiteStands: process.env.TOOL_GET_SITE_STANDS_METHOD || 'GET',
    getSiteResults: process.env.TOOL_GET_SITE_RESULTS_METHOD || 'GET',
    createFairRubric: process.env.TOOL_CREATE_FAIR_RUBRIC_METHOD || 'POST',
    addFairRubricCriterion: process.env.TOOL_ADD_FAIR_RUBRIC_CRITERION_METHOD || 'POST',
    createFair: process.env.TOOL_CREATE_FAIR_METHOD || 'POST',
    updateFair: process.env.TOOL_UPDATE_FAIR_METHOD || 'PUT',
    changeFairStatus: process.env.TOOL_CHANGE_FAIR_STATUS_METHOD || 'POST',
    listFairStands: process.env.TOOL_LIST_FAIR_STANDS_METHOD || 'GET',
    createFairStand: process.env.TOOL_CREATE_FAIR_STAND_METHOD || 'POST',
    updateFairStand: process.env.TOOL_UPDATE_FAIR_STAND_METHOD || 'PUT',
    deleteFairStand: process.env.TOOL_DELETE_FAIR_STAND_METHOD || 'DELETE',
  },
});

export const assertProductionConfiguration = () => {
  if (config.nodeEnv !== 'production') return;
  required('REDIS_URL', config.redisUrl);
  required('AI_CONTEXT_PRIVATE_KEY_PATH', config.contextPrivateKey);
  required('AI_CONTEXT_PUBLIC_KEY_PATH', config.contextPublicKey);
  if (config.serviceToken.length < 32) throw new Error('AI_SERVICE_TOKEN must contain at least 32 characters');
  if (config.mtlsRequired && (!config.mtlsCaPath || !config.mtlsCertPath || !config.mtlsKeyPath)) {
    throw new Error('AI_MTLS_CA_PATH, AI_MTLS_CERT_PATH and AI_MTLS_KEY_PATH are required when mTLS is enabled');
  }
};
