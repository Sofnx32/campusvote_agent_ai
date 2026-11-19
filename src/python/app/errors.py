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


# Códigos canónicos de Fase 1. Los alias `LLM_*` se conservan para no romper
# clientes que ya los manejan; el proveedor Python emite los códigos canónicos.
PHASE1_PROVIDER_CODES = frozenset({
    "RATE_LIMITED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE",
    "PROVIDER_AUTH_FAILED", "LLM_OUTPUT_TOKEN_LIMIT", "LLM_MODEL_NOT_FOUND",
    "LLM_INVALID_RESPONSE",
})
LEGACY_PROVIDER_CODES = frozenset({
    "LLM_RATE_LIMITED", "LLM_PROVIDER_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE",
    "LLM_AUTH_FAILED", "LLM_PROVIDER_AUTH_FAILED",
})

# Códigos de validación de herramientas (Fase 2.6)
TOOL_VALIDATION_CODES = frozenset({
    "TOOL_NOT_FOUND",         # No existe en el registro canónico (TOOLS)
    "TOOL_NOT_ALLOWED",       # Existe en TOOLS pero no en allowlist del ciclo
    "TOOLS_DISABLED",         # Decision Layer deshabilitó herramientas (allow_tools=False)
    "TOOL_LIMIT_EXCEEDED",    # Supera max_tool_calls configurado
    "INVALID_TOOL_CALL",      # Estructura inválida (parser)
})

ERROR_CATALOG = {
    "AUTH": {"AI_SERVICE_UNAUTHORIZED", "AI_CONTEXT_INVALID"},
    "AUTHORIZATION": {"AI_SCOPE_FORBIDDEN", "AI_ORGANIZATION_REQUIRED", "AI_SITE_ASSIGNMENT_REQUIRED"},
    "VALIDATION": {"AI_INVALID_REQUEST", "VALIDATION_INVALID_JSON", "VALIDATION_INVALID_REQUEST", "AI_RESPONSE_INVALID"},
    "NETWORK": {"AI_SERVICE_TIMEOUT", "AI_SERVICE_UNAVAILABLE", "AI_DEADLINE_EXCEEDED"},
    "AI_PROVIDER": (
        PHASE1_PROVIDER_CODES | LEGACY_PROVIDER_CODES | {"LLM_MODEL_NOT_FOUND", "LLM_INVALID_RESPONSE"}
    ),
    "AI_SERVICE": {"AI_TOOL_TIMEOUT", "AI_TOOL_UNAVAILABLE", "TOOL_EXECUTION_FAILED", "AI_SERVICE_EMPTY_RESPONSE", "AI_SERVICE_INVALID_RESPONSE", "AI_BACKEND_EMPTY_RESPONSE", "AI_BACKEND_UNAVAILABLE", "AI_BACKEND_INVALID_RESPONSE"},
    "DATABASE": {"DATABASE_UNAVAILABLE", "DATABASE_OPERATION_FAILED"},
    "REDIS": {"REDIS_UNAVAILABLE", "AI_CONTEXT_REPLAY_STORE_UNAVAILABLE"},
    "BUSINESS_OPERATION": {"AI_CONFIRMATION_REQUIRED", "AI_OPERATION_CONFLICT", "AI_RESOURCE_NOT_FOUND"},
    "SESSION": {"SESSION_EXPIRED", "SESSION_REVOKED", "AI_CONTEXT_EXPIRED"},
    "INTERNAL": {"AI_INTERNAL_ERROR"},
    "TOOL_VALIDATION": TOOL_VALIDATION_CODES,
}

# Errores de negocio que el modelo puede corregir en el mismo turno (por
# ejemplo, una fecha con formato inválido o sin hora).
CORRECTABLE_CODES = {
    "AI_INVALID_REQUEST",
    "AI_CONFIRMATION_REQUIRED",
    "AI_OPERATION_CONFLICT",
    "AI_RESOURCE_NOT_FOUND",
    "INVALID_TOOL_CALL",
    "TOOL_NOT_ALLOWED",
    "TOOL_LIMIT_EXCEEDED",
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
    # Canonical Phase 1 codes
    "RATE_LIMITED": "El asistente está recibiendo muchas solicitudes. Inténtalo más tarde.",
    "PROVIDER_TIMEOUT": "El asistente tardó demasiado en responder.",
    "PROVIDER_UNAVAILABLE": "El asistente está temporalmente fuera de servicio.",
    "PROVIDER_AUTH_FAILED": "El asistente está temporalmente fuera de servicio.",
    "TOOL_EXECUTION_FAILED": "No se pudo completar la operación. Inténtalo nuevamente.",
    "AI_INTERNAL_ERROR": "Ocurrió un problema inesperado.",
    "SESSION_EXPIRED": "Tu sesión de administrador ha expirado. Vuelve a iniciar sesión.",
    "SESSION_REVOKED": "Tu sesión ha sido cerrada. Vuelve a iniciar sesión para continuar.",
    "AI_CONTEXT_EXPIRED": "El contexto de la operación ha expirado. La operación se reintentará automáticamente.",
    # Phase 1 new messages
    "LLM_OUTPUT_TOKEN_LIMIT": "La respuesta del asistente fue truncada por exceder el límite de tokens. Inténtalo de nuevo con una solicitud más específica.",
    "LLM_PROVIDER_AUTH_FAILED": "Error de autenticación con el proveedor del modelo. Contacta al administrador.",
    # Phase 2.6: Tool validation messages
    "TOOL_NOT_FOUND": "La herramienta solicitada no existe.",
    "TOOL_NOT_ALLOWED": "La herramienta solicitada no está disponible para esta consulta.",
    "TOOLS_DISABLED": "Las herramientas están deshabilitadas para este turno.",
    "TOOL_LIMIT_EXCEEDED": "Se ha superado el límite de herramientas por turno.",
    "INVALID_TOOL_CALL": "La llamada a herramienta tiene una estructura inválida.",
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