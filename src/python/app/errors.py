from __future__ import annotations


class AgentError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 500,
        retryable: bool = False,
        *,
        public_message: str | None = None,
        details: object | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.public_message = public_message
        self.details = details


ERROR_CATALOG = {
    "AUTH": {"AI_SERVICE_UNAUTHORIZED", "AI_CONTEXT_INVALID"},
    "AUTHORIZATION": {"AI_SCOPE_FORBIDDEN", "AI_ORGANIZATION_REQUIRED", "AI_SITE_ASSIGNMENT_REQUIRED"},
    "VALIDATION": {"AI_INVALID_REQUEST", "VALIDATION_INVALID_JSON", "VALIDATION_INVALID_REQUEST", "AI_RESPONSE_INVALID"},
    "NETWORK": {"AI_SERVICE_TIMEOUT", "AI_SERVICE_UNAVAILABLE", "AI_DEADLINE_EXCEEDED"},
    "AI_PROVIDER": {"LLM_AUTH_FAILED", "LLM_MODEL_NOT_FOUND", "LLM_RATE_LIMITED", "LLM_PROVIDER_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE", "LLM_INVALID_RESPONSE"},
    "AI_SERVICE": {"AI_TOOL_TIMEOUT", "AI_TOOL_UNAVAILABLE", "AI_SERVICE_EMPTY_RESPONSE", "AI_SERVICE_INVALID_RESPONSE", "AI_BACKEND_EMPTY_RESPONSE", "AI_BACKEND_UNAVAILABLE", "AI_BACKEND_INVALID_RESPONSE"},
    "DATABASE": {"DATABASE_UNAVAILABLE", "DATABASE_OPERATION_FAILED"},
    "REDIS": {"REDIS_UNAVAILABLE", "AI_CONTEXT_REPLAY_STORE_UNAVAILABLE"},
    "BUSINESS_OPERATION": {"AI_CONFIRMATION_REQUIRED", "AI_OPERATION_CONFLICT", "AI_RESOURCE_NOT_FOUND"},
    "INTERNAL": {"AI_INTERNAL_ERROR"},
}

# Errores de negocio que el modelo puede corregir en el mismo turno (por
# ejemplo, una fecha con formato inválido o sin hora).
CORRECTABLE_CODES = {
    "AI_INVALID_REQUEST",
    "AI_CONFIRMATION_REQUIRED",
    "AI_OPERATION_CONFLICT",
    "AI_RESOURCE_NOT_FOUND",
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
    "AI_DEADLINE_EXCEEDED": "El asistente tardó demasiado en responder. Inténtalo nuevamente.",
    "LLM_PROVIDER_UNAVAILABLE": "El asistente está temporalmente fuera de servicio.",
    "LLM_INVALID_RESPONSE": "El asistente no pudo procesar una respuesta válida. Inténtalo nuevamente.",
    "AI_INTERNAL_ERROR": "Ocurrió un problema inesperado.",
}


def error_payload(error: AgentError, correlation_id: str | None) -> dict:
    fallback = "Ocurrió un problema inesperado." if error.status >= 500 else error.message
    return {
        "success": False,
        "error": {
            "code": error.code,
            "message": error.public_message or PUBLIC_MESSAGES.get(error.code, fallback),
            "retryable": error.retryable,
            "requestId": correlation_id,
            "details": error.details,
        },
        "requestId": correlation_id,
        "meta": {"correlation_id": correlation_id},
    }
