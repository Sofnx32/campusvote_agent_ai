"""Forensic Observability (Fase 2.7).

Instrumentación estructurada, ligera y correlacionada de extremo a extremo.
No altera comportamiento funcional ni contratos existentes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .observability import request_id_context, metrics_context, current_metrics, RequestMetrics

# Logger dedicado para eventos forenses
forensic_logger = logging.getLogger("campusvote.forensic")
forensic_logger.setLevel(logging.INFO)
if not forensic_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    forensic_logger.addHandler(handler)
    forensic_logger.propagate = False

# ContextVar para correlation_id (compartido Node↔Python)
correlation_id_context: ContextVar[str] = ContextVar("campusvote_correlation_id", default="-")

# Claves de datos sensibles para redacción centralizada
_SENSITIVE_KEYS = frozenset({
    "authorization", "Authorization", "api_key", "apiKey", "apikey",
    "token", "jwt", "password", "secret", "key", "private",
    "credit_card", "ssn", "dni", "passport", "email", "phone",
    "address", "ip", "ip_address", "user_agent", "cookie",
    "session", "auth", "bearer", "access_token", "refresh_token",
    "client_secret", "client_id", "api_secret", "signing_key",
    "private_key", "public_key", "certificate", "pem", "crt",
})
# Campos de herramientas que nunca se loguean completos
_TOOL_SENSITIVE_FIELDS = frozenset({
    "arguments", "result", "response", "payload", "body",
    "content", "message", "history", "messages", "prompt",
})


def _generate_request_id() -> str:
    """Genera un request_id único (UUID4 hex)."""
    return uuid.uuid4().hex


def get_correlation_id() -> str:
    """Obtiene el correlation_id actual del contexto."""
    return correlation_id_context.get()


def get_request_id() -> str:
    """Obtiene el request_id actual del contexto."""
    return request_id_context.get()


def set_correlation_id(correlation_id: str) -> ContextVar.Token:
    """Establece el correlation_id en el contexto."""
    return correlation_id_context.set(correlation_id)


def _sanitize_value(value: Any, max_len: int = 1024) -> Any:
    """Redacta valores sensibles y trunca longitud."""
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + "...[truncated]"
        return value
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        return _sanitize_dict(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v, max_len) for v in value[:100]]  # Límite de items
    return str(value)[:max_len]


def _sanitize_dict(data: dict, max_len: int = 1024) -> dict:
    """Redacta claves sensibles en un diccionario."""
    if not isinstance(data, dict):
        return {}
    sanitized = {}
    for k, v in data.items():
        key_lower = str(k).lower()
        if any(s in key_lower for s in _SENSITIVE_KEYS):
            sanitized[k] = "[REDACTED]"
        elif any(s in key_lower for s in _TOOL_SENSITIVE_FIELDS):
            # Para campos de herramientas, solo metadatos
            if isinstance(v, dict):
                sanitized[k] = {sk: "[REDACTED]" if isinstance(sv, (str, dict, list)) else sv
                               for sk, sv in list(v.items())[:10]}
            elif isinstance(v, list):
                sanitized[k] = f"[{len(v)} items]"
            elif isinstance(v, str) and len(v) > 100:
                sanitized[k] = v[:100] + "...[truncated]"
            else:
                sanitized[k] = v
        else:
            sanitized[k] = _sanitize_value(v, max_len)
    return sanitized


def _build_base_event(event_type: str, **extra) -> dict:
    """Construye la base común de todos los eventos forenses."""
    request_id = get_request_id()
    correlation_id = get_correlation_id()
    return {
        "event": event_type,
        "timestamp": time.time(),
        "request_id": request_id,
        "correlation_id": correlation_id,
        **extra,
    }


def _safe_log(event: dict) -> None:
    """Log seguro: nunca interrumpe el flujo funcional."""
    try:
        forensic_logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Silenciar errores de logging - no deben romper la funcionalidad
        pass


# =============================================================================
# EVENTOS DEL CICLO DE VIDA (A-I)
# =============================================================================

# A. Recepción
def log_request_received(
    endpoint: str,
    validation_result: str,  # "valid" | "invalid"
    context_keys: list[str] | None = None,
) -> None:
    """A. Recepción: request_received."""
    event = _build_base_event(
        "request_received",
        endpoint=endpoint,
        validation_result=validation_result,
        context_keys=context_keys or [],
    )
    _safe_log(event)


# B. Historial/Memoria
def log_history_loaded(message_count: int, source: str = "conversation_store") -> None:
    """B. Historial: history_loaded."""
    event = _build_base_event(
        "history_loaded",
        history_message_count=message_count,
        source=source,
    )
    _safe_log(event)


def log_conversation_memory_rebuilt(turn_count: int, source: str = "conversation_store") -> None:
    """B. Memoria: conversation_memory_rebuilt."""
    event = _build_base_event(
        "conversation_memory_rebuilt",
        turn_count=turn_count,
        source=source,
    )
    _safe_log(event)


def log_turn_context_resolved(
    resolved_reference_count: int,
    ambiguous_reference_count: int,
    selected_entity_count: int,
    last_query_fresh: bool,
    last_query_tool: str | None = None,
) -> None:
    """B. Contexto: turn_context_resolved."""
    event = _build_base_event(
        "turn_context_resolved",
        resolved_reference_count=resolved_reference_count,
        ambiguous_reference_count=ambiguous_reference_count,
        selected_entity_count=selected_entity_count,
        last_query_fresh=last_query_fresh,
        last_query_tool=last_query_tool,
    )
    _safe_log(event)


# C. Decision Layer
def log_decision_completed(
    decision_category: str,
    decision_reason: str,
    allow_tools: bool,
    needs_clarification: bool,
    selected_tool_count: int = 0,
) -> None:
    """C. Decision Layer: decision_completed."""
    event = _build_base_event(
        "decision_completed",
        decision_category=decision_category,
        decision_reason=decision_reason,
        allow_tools=allow_tools,
        needs_clarification=needs_clarification,
        selected_tool_count=selected_tool_count,
    )
    _safe_log(event)


# D. Direct Response
def log_direct_response_evaluated(
    direct_response_state: str | None,
    direct_response_reason: str | None,
    direct_response_source: str | None,
    llm_skipped: bool,
    tool_execution_skipped: bool,
) -> None:
    """D. Direct Response: direct_response_evaluated."""
    event = _build_base_event(
        "direct_response_evaluated",
        direct_response_state=direct_response_state,
        direct_response_reason=direct_response_reason,
        direct_response_source=direct_response_source,
        llm_skipped=llm_skipped,
        tool_execution_skipped=tool_execution_skipped,
    )
    _safe_log(event)


def log_direct_response_used(
    direct_response_state: str,
    direct_response_reason: str,
    direct_response_source: str,
) -> None:
    """D. Direct Response: direct_response_used (ANSWERED)."""
    event = _build_base_event(
        "direct_response_used",
        direct_response_state=direct_response_state,
        direct_response_reason=direct_response_reason,
        direct_response_source=direct_response_source,
        llm_skipped=True,
        tool_execution_skipped=True,
    )
    _safe_log(event)


# E. Selección de herramientas
def log_tool_selection_completed(
    selected_tool_count: int,
    selected_tool_names: list[str],
    selection_reason: str,
    allowed_tool_count: int,
) -> None:
    """E. Selección: tool_selection_completed."""
    event = _build_base_event(
        "tool_selection_completed",
        selected_tool_count=selected_tool_count,
        selected_tool_names=selected_tool_names,
        selection_reason=selection_reason,
        allowed_tool_count=allowed_tool_count,
    )
    _safe_log(event)


# F. Proveedor LLM
def log_llm_request_started(
    model: str,
    is_fallback: bool,
    attempt: int,
    allowed_tool_count: int,
) -> None:
    """F. LLM: llm_request_started."""
    event = _build_base_event(
        "llm_request_started",
        model=model,
        is_fallback=is_fallback,
        attempt=attempt,
        allowed_tool_count=allowed_tool_count,
    )
    _safe_log(event)


def log_llm_request_completed(
    model: str,
    is_fallback: bool,
    attempt: int,
    duration_ms: float,
    response_tool_call_count: int,
    response_has_tool_calls: bool,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """F. LLM: llm_request_completed."""
    event = _build_base_event(
        "llm_request_completed",
        model=model,
        is_fallback=is_fallback,
        attempt=attempt,
        llm_duration_ms=round(duration_ms, 2),
        response_tool_call_count=response_tool_call_count,
        response_has_tool_calls=response_has_tool_calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    _safe_log(event)


def log_llm_request_failed(
    model: str,
    is_fallback: bool,
    attempt: int,
    duration_ms: float,
    provider_error_code: str,
    retry_attempt: int = 0,
) -> None:
    """F. LLM: llm_request_failed."""
    event = _build_base_event(
        "llm_request_failed",
        model=model,
        is_fallback=is_fallback,
        attempt=attempt,
        llm_duration_ms=round(duration_ms, 2),
        provider_error_code=provider_error_code,
        retry_attempt=retry_attempt,
        response_tool_call_count=0,
        response_has_tool_calls=False,
    )
    _safe_log(event)


# G. Validación de herramientas
def log_tool_validation_started(
    tool_name: str,
    tool_call_index: int,
    allowed_tool_count: int,
) -> None:
    """G. Validación: tool_validation_started."""
    event = _build_base_event(
        "tool_validation_started",
        tool_name=tool_name,
        tool_call_index=tool_call_index,
        allowed_tool_count=allowed_tool_count,
    )
    _safe_log(event)


def log_tool_validation_passed(
    tool_name: str,
    tool_call_index: int,
) -> None:
    """G. Validación: tool_validation_passed."""
    event = _build_base_event(
        "tool_validation_passed",
        tool_name=tool_name,
        tool_call_index=tool_call_index,
    )
    _safe_log(event)


def log_tool_validation_rejected(
    validation_error_code: str,
    tool_name: str,
    tool_call_index: int,
    allowed_tools: list[str] | None = None,
) -> None:
    """G. Validación: tool_validation_rejected."""
    event = _build_base_event(
        "tool_validation_rejected",
        validation_error_code=validation_error_code,
        tool_name=tool_name,
        tool_call_index=tool_call_index,
        allowed_tools=allowed_tools or [],
    )
    _safe_log(event)


# H. Ejecución de herramientas
def log_tool_execution_started(
    tool_name: str,
    tool_call_index: int,
    tool_call_id: str,
) -> None:
    """H. Ejecución: tool_execution_started."""
    event = _build_base_event(
        "tool_execution_started",
        tool_name=tool_name,
        tool_call_index=tool_call_index,
        tool_call_id=tool_call_id,
    )
    _safe_log(event)


def log_tool_execution_completed(
    tool_name: str,
    tool_call_index: int,
    tool_call_id: str,
    duration_ms: float,
    functional_status: str,  # "success" | "confirmation_required" | "error"
    error_code: str | None = None,
) -> None:
    """H. Ejecución: tool_execution_completed."""
    event = _build_base_event(
        "tool_execution_completed",
        tool_name=tool_name,
        tool_call_index=tool_call_index,
        tool_call_id=tool_call_id,
        tool_execution_duration_ms=round(duration_ms, 2),
        functional_status=functional_status,
        error_code=error_code,
    )
    _safe_log(event)


def log_tool_execution_failed(
    tool_name: str,
    tool_call_index: int,
    tool_call_id: str,
    duration_ms: float,
    error_type: str,
    error_code: str | None = None,
) -> None:
    """H. Ejecución: tool_execution_failed."""
    event = _build_base_event(
        "tool_execution_failed",
        tool_name=tool_name,
        tool_call_index=tool_call_index,
        tool_call_id=tool_call_id,
        tool_execution_duration_ms=round(duration_ms, 2),
        error_type=error_type,
        error_code=error_code,
    )
    _safe_log(event)


# I. Persistencia y respuesta
def log_conversation_turn_persisted(
    message_count: int,
    has_tool_calls: bool,
    pending_operation: bool,
    confirmation_state: str,
) -> None:
    """I. Persistencia: conversation_turn_persisted."""
    event = _build_base_event(
        "conversation_turn_persisted",
        message_count=message_count,
        has_tool_calls=has_tool_calls,
        pending_operation=pending_operation,
        confirmation_state=confirmation_state,
    )
    _safe_log(event)


def log_response_built(
    has_ui: bool,
    has_operation: bool,
    tool_calls_count: int,
    is_direct_response: bool,
) -> None:
    """I. Respuesta: response_built."""
    event = _build_base_event(
        "response_built",
        has_ui=has_ui,
        has_operation=has_operation,
        tool_calls_count=tool_calls_count,
        is_direct_response=is_direct_response,
    )
    _safe_log(event)


def log_request_completed(
    total_duration_ms: float,
    stage_durations: dict[str, float],
    outcome: str,  # "success" | "direct_response" | "agent_response"
) -> None:
    """I. Completado: request_completed."""
    event = _build_base_event(
        "request_completed",
        total_duration_ms=round(total_duration_ms, 2),
        stage_durations=stage_durations,
        outcome=outcome,
    )
    _safe_log(event)


def log_request_failed(
    total_duration_ms: float,
    stage_durations: dict[str, float],
    error_code: str,
    error_category: str,  # "auth" | "functional" | "selection" | "tool_rejected" | "provider" | "timeout" | "deadline" | "cancellation" | "redis" | "persistence" | "internal"
) -> None:
    """I. Fallido: request_failed."""
    event = _build_base_event(
        "request_failed",
        total_duration_ms=round(total_duration_ms, 2),
        stage_durations=stage_durations,
        error_code=error_code,
        error_category=error_category,
    )
    _safe_log(event)


# =============================================================================
# HELPERS DE DURACIÓN
# =============================================================================

@dataclass
class StageTimer:
    """Timer para medir duraciones de etapas sin afectar deadlines."""
    stage_name: str
    started: float = field(default_factory=time.monotonic)

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000


def measure_stage(stage_name: str):
    """Context manager para medir duración de una etapa."""
    started = time.monotonic()
    try:
        yield
    finally:
        duration = (time.monotonic() - started) * 1000
        # Se registra via log_request_completed/failed al final
        pass


# =============================================================================
# CLASIFICACIÓN DE ERRORES PARA CATEGORIZACIÓN FORENSE
# =============================================================================

ERROR_CATEGORIES = {
    # Auth
    "AI_SERVICE_UNAUTHORIZED": "auth",
    "AI_CONTEXT_INVALID": "auth",
    "AI_SCOPE_FORBIDDEN": "auth",
    "AI_ORGANIZATION_REQUIRED": "auth",
    "AI_SITE_ASSIGNMENT_REQUIRED": "auth",
    # Funcionales
    "AI_INVALID_REQUEST": "functional",
    "VALIDATION_INVALID_JSON": "functional",
    "VALIDATION_INVALID_REQUEST": "functional",
    "AI_RESPONSE_INVALID": "functional",
    "AI_CONFIRMATION_REQUIRED": "functional",
    "AI_OPERATION_CONFLICT": "functional",
    "AI_RESOURCE_NOT_FOUND": "functional",
    # Selección
    "TOOL_NOT_ALLOWED": "tool_rejected",
    "TOOLS_DISABLED": "tool_rejected",
    "TOOL_LIMIT_EXCEEDED": "tool_rejected",
    # Proveedor
    "RATE_LIMITED": "provider",
    "PROVIDER_TIMEOUT": "provider",
    "PROVIDER_UNAVAILABLE": "provider",
    "PROVIDER_AUTH_FAILED": "provider",
    "LLM_MODEL_NOT_FOUND": "provider",
    "LLM_INVALID_RESPONSE": "provider",
    "LLM_OUTPUT_TOKEN_LIMIT": "provider",
    # Timeouts
    "AI_DEADLINE_EXCEEDED": "deadline",
    "AI_TOOL_TIMEOUT": "timeout",
    # Cancelación
    "TOOL_EXECUTION_FAILED": "cancellation",
    # Redis
    "REDIS_UNAVAILABLE": "redis",
    "AI_CONTEXT_REPLAY_STORE_UNAVAILABLE": "redis",
    # Persistencia
    "AI_INTERNAL_ERROR": "internal",
}


def categorize_error(error_code: str) -> str:
    """Clasifica un código de error en categoría forense."""
    return ERROR_CATEGORIES.get(error_code, "unknown")


# =============================================================================
# INTEGRACIÓN CON EXISTING OBSERVABILITY
# =============================================================================

def enhance_request_metrics(metrics: RequestMetrics) -> RequestMetrics:
    """Enriquece RequestMetrics con datos forenses adicionales."""
    # RequestMetrics ya tiene la mayoría de contadores necesarios
    # Esta función es un placeholder para extensiones futuras
    return metrics


def log_forensic_summary(metrics: RequestMetrics, error: Exception | None = None) -> None:
    """Log final correlacionado con métricas y error opcional."""
    correlation_id = get_correlation_id()
    request_id = get_request_id()
    
    if error:
        error_code = getattr(error, "code", "AI_INTERNAL_ERROR")
        error_category = categorize_error(error_code)
        log_request_failed(
            total_duration_ms=metrics.to_payload()["latency_ms"]["total"],
            stage_durations={},  # Se llenaría con StageTimer real
            error_code=error_code,
            error_category=error_category,
        )
    else:
        log_request_completed(
            total_duration_ms=metrics.to_payload()["latency_ms"]["total"],
            stage_durations={},
            outcome="success",
        )