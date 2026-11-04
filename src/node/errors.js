export class AgentError extends Error {
  constructor(code, message, status = 500, retryable = false) {
    super(message);
    this.name = 'AgentError';
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

export const errorResponse = (error, correlationId) => ({
  success: false,
  error: {
    code: error.code || 'AI_INTERNAL_ERROR',
    message: error.message || 'Internal agent error',
    retryable: Boolean(error.retryable),
  },
  meta: { correlation_id: correlationId },
});
