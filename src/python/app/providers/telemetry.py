"""Logs JSON correlacionados del proveedor (sin secretos ni PII)."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..observability import request_id_context
from .retry_policy import total_attempts

logger = logging.getLogger(__name__)


def emit_attempt_log(
    *,
    request_id: str,
    phase: str,
    model: str,
    attempt: int,
    max_retries: int,
    latency_ms: float,
    is_fallback: bool,
    usage: dict | None = None,
    error_code: str | None = None,
    origin_status: int | None = None,
) -> None:
    """Un único log por intento: nunca incluye URLs, headers ni credenciales."""
    payload: dict[str, Any] = {
        "event": "llm_attempt",
        "requestId": request_id,
        "correlation_id": request_id,
        "phase": phase,
        "model": model,
        "attempt": attempt,
        "max_attempts": total_attempts(max_retries),
        "latency_ms": round(latency_ms, 2),
        "fallback_used": is_fallback,
    }
    if usage:
        payload["usage"] = usage
    if error_code:
        payload["error_code"] = error_code
    if origin_status is not None:
        payload["origin_status"] = origin_status
    logger.info(json.dumps(payload, ensure_ascii=False))


def emit_event(level: str, event: str, **fields: Any) -> None:
    """Evento estructurado con requestId correlacionado."""
    payload = {"event": event, "requestId": request_id_context.get(), **fields}
    getattr(logger, level)(json.dumps(payload, ensure_ascii=False))
