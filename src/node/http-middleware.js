import crypto from 'node:crypto';
import { AgentError, errorResponse } from './errors.js';

export const jsonParserErrorHandler = (error, req, res, next) => {
  if (error?.type !== 'entity.parse.failed' && !(error instanceof SyntaxError && Object.hasOwn(error, 'body'))) return next(error);
  const requestId = req.requestId || crypto.randomUUID();
  console.warn(JSON.stringify({
    event: 'request_failed', service: 'agent-node', requestId,
    method: req.method, route: req.route?.path || req.path,
    status: 400, code: 'VALIDATION_INVALID_JSON', cause: error?.name || 'SyntaxError',
  }));
  if (!res.headersSent) {
    res.setHeader('X-Request-Id', requestId);
    res.setHeader('X-Correlation-Id', requestId);
    res.status(400).json(errorResponse(
      new AgentError('VALIDATION_INVALID_JSON', 'Revisa la información ingresada.', 400),
      requestId,
    ));
  }
};
