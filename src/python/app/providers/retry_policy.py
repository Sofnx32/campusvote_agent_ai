"""Política de reintentos: estados transitorios, backoff y presupuesto."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..errors import AgentError

# Solo estos estados HTTP de origen son transitorios. El mapeo público de
# PROVIDER_AUTH_FAILED es 502, pero su origen 401/403 NUNCA entra aquí.
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
NON_RETRYABLE_HTTP_STATUSES = frozenset({400, 401, 403, 404, 422})
RETRYABLE_ERROR_CODES = frozenset({
    "RATE_LIMITED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE",
})
FALLBACK_ERROR_CODES = frozenset({
    "RATE_LIMITED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE",
})
MAX_TOTAL_ATTEMPTS = 3


def total_attempts(max_retries: int) -> int:
    """Máximo 3 intentos totales: 1 inicial + como máximo 2 reintentos."""
    return max(1, min(1 + int(max_retries), MAX_TOTAL_ATTEMPTS))


def parse_retry_after(value: str | None) -> float | None:
    """Acepta segundos o fecha HTTP (RFC 7231). Devuelve None si es inválido."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at is None:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_backoff(
    attempt: int,
    base_delay: float,
    max_delay: float,
    retry_after: float | None = None,
) -> float:
    """Exponencial con jitter completo; Retry-After manda sobre el cálculo."""
    if retry_after is not None and retry_after > 0:
        # No recortamos Retry-After: respetarlo tiene prioridad sobre max_delay.
        return retry_after + random.uniform(0, 0.25)
    capped = min(max(0.0, base_delay) * (2 ** attempt), max(0.0, max_delay))
    return random.uniform(0, capped)


def is_retryable(error: AgentError) -> bool:
    """Solo errores transitorios conocidos pueden reintentarse."""
    details = error.details if isinstance(error.details, dict) else {}
    origin_status = details.get("origin_status")
    if origin_status is not None:
        return isinstance(origin_status, int) and origin_status in RETRYABLE_HTTP_STATUSES
    return error.code in RETRYABLE_ERROR_CODES and bool(error.retryable)


async def sleep_with_budget_check(delay: float, budget_ends: float) -> None:
    """Duerme como máximo hasta el presupuesto disponible."""
    remaining = budget_ends - time.monotonic()
    if delay >= remaining:
        raise AgentError(
            "AI_DEADLINE_EXCEEDED", "Insufficient time budget for provider backoff", 504, True,
            details={"wait_seconds": round(delay, 2), "remaining_seconds": round(remaining, 2)},
        )
    await asyncio.sleep(max(0.0, delay))
