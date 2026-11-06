from __future__ import annotations


class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 500, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


ERROR_CATALOG = {
    "AUTH": {"AI_SERVICE_UNAUTHORIZED", "AI_CONTEXT_INVALID"},
    "AUTHORIZATION": {"AI_SCOPE_FORBIDDEN", "AI_ORGANIZATION_REQUIRED", "AI_SITE_ASSIGNMENT_REQUIRED"},
    "VALIDATION": {"AI_INVALID_REQUEST", "VALIDATION_INVALID_JSON", "VALIDATION_INVALID_REQUEST", "AI_RESPONSE_INVALID"},
    "NETWORK": {"AI_SERVICE_TIMEOUT", "AI_SERVICE_UNAVAILABLE"},
    "AI_PROVIDER": {"LLM_AUTH_FAILED", "LLM_MODEL_NOT_FOUND", "LLM_RATE_LIMITED", "LLM_PROVIDER_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE", "LLM_INVALID_RESPONSE"},
    "AI_SERVICE": {"AI_TOOL_TIMEOUT", "AI_TOOL_UNAVAILABLE", "AI_SERVICE_EMPTY_RESPONSE", "AI_SERVICE_INVALID_RESPONSE", "AI_BACKEND_EMPTY_RESPONSE", "AI_BACKEND_UNAVAILABLE", "AI_BACKEND_INVALID_RESPONSE"},
    "DATABASE": {"DATABASE_UNAVAILABLE", "DATABASE_OPERATION_FAILED"},
    "REDIS": {"REDIS_UNAVAILABLE", "AI_CONTEXT_REPLAY_STORE_UNAVAILABLE"},
    "BUSINESS_OPERATION": {"AI_CONFIRMATION_REQUIRED", "AI_OPERATION_CONFLICT", "AI_RESOURCE_NOT_FOUND"},
    "INTERNAL": {"AI_INTERNAL_ERROR"},
}

PUBLIC_MESSAGES = {
    "AI_SERVICE_UNAUTHORIZED": "No fue posible validar la solicitud del asistente.",
    "AI_CONTEXT_INVALID": "No fue posible validar tu contexto institucional. Vuelve a intentarlo.",
    "AI_SCOPE_FORBIDDEN": "No tienes permisos para realizar esta operación.",
    "AI_INVALID_REQUEST": "Revisa la información ingresada.",
    "LLM_AUTH_FAILED": "El asistente está temporalmente fuera de servicio.",
    "LLM_MODEL_NOT_FOUND": "El asistente está temporalmente fuera de servicio.",
    "LLM_RATE_LIMITED": "El asistente está recibiendo muchas solicitudes. Inténtalo más tarde.",
    "LLM_PROVIDER_TIMEOUT": "El asistente tardó demasiado en responder.",
    "LLM_PROVIDER_UNAVAILABLE": "El asistente está temporalmente fuera de servicio.",
    "LLM_INVALID_RESPONSE": "El asistente no pudo procesar una respuesta válida. Inténtalo nuevamente.",
    "AI_INTERNAL_ERROR": "Ocurrió un problema inesperado.",
}


def error_payload(error: AgentError, correlation_id: str | None) -> dict:
    return {
        "success": False,
        "error": {
            "code": error.code,
            "message": PUBLIC_MESSAGES.get(error.code, "Ocurrió un problema inesperado." if error.status >= 500 else error.message),
            "retryable": error.retryable,
            "requestId": correlation_id,
            "details": None,
        },
        "requestId": correlation_id,
        "meta": {"correlation_id": correlation_id},
    }
