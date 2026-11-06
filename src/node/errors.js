import { PUBLIC_ERROR_MESSAGES } from './error-catalog.js';

export class AgentError extends Error {
  constructor(code, message, status = 500, retryable = false, options = {}) {
    super(message);
    this.name = 'AgentError';
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    if (options.cause) this.cause = options.cause;
  }
}

export const errorResponse = (error, correlationId) => ({
  success: false,
  error: {
    code: error.code || 'AI_INTERNAL_ERROR',
    message: error.publicMessage || error.message || 'Ocurrió un problema inesperado.',
    retryable: Boolean(error.retryable),
    requestId: correlationId,
    details: null,
  },
  requestId: correlationId,
  meta: { correlation_id: correlationId },
});

export const publicError = (error) => {
  const cause = error?.cause;
  if (error?.name === 'AbortError' || error?.name === 'TimeoutError' || cause?.name === 'AbortError' || cause?.name === 'TimeoutError') {
    return new AgentError('AI_SERVICE_TIMEOUT', 'El asistente tardó demasiado en responder.', 504, true);
  }
  if (['ECONNREFUSED', 'ECONNRESET', 'ENOTFOUND', 'EAI_AGAIN'].includes(error?.code || cause?.code) || error?.name === 'TypeError' && cause?.code) {
    return new AgentError('AI_SERVICE_UNAVAILABLE', 'El asistente está temporalmente fuera de servicio.', 503, true);
  }
  if (error?.code === 'ECONNABORTED' || error?.name === 'MaxRetriesPerRequestError') {
    return new AgentError('REDIS_UNAVAILABLE', 'El asistente está temporalmente fuera de servicio.', 503, true);
  }
  if (error instanceof AgentError) {
    return new AgentError(error.code, PUBLIC_ERROR_MESSAGES[error.code] || 'Ocurrió un problema inesperado.', error.status || 500, error.retryable);
  }
  return new AgentError('AI_INTERNAL_ERROR', 'Ocurrió un problema inesperado.', 500);
};
