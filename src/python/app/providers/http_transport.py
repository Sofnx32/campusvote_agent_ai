"""Transporte HTTP de una sola llamada al proveedor (sin reintentos)."""

from __future__ import annotations

import json
import time
from typing import FrozenSet

import httpx

from ..errors import AgentError
from ..models import LLMResult
from .response_parser import parse_response
from .retry_policy import NON_RETRYABLE_HTTP_STATUSES, RETRYABLE_HTTP_STATUSES, parse_retry_after
from .telemetry import emit_event


def _http_error(
    code: str,
    public_status: int,
    origin_status: int,
    retryable: bool,
    details: dict | None = None,
) -> AgentError:
    """Errores de proveedor jamás se exponen como 401 de usuario."""
    return AgentError(
        code, "LLM provider request failed", public_status, retryable,
        details={"origin_status": origin_status, **(details or {})},
    )


def _map_http_status(status: int, retry_after: float | None) -> AgentError:
    details = {"retry_after_seconds": retry_after} if retry_after is not None else None
    if status in (401, 403):
        return _http_error("PROVIDER_AUTH_FAILED", 502, status, False, details)
    if status == 404:
        return _http_error("LLM_MODEL_NOT_FOUND", 502, status, False, details)
    if status == 429:
        return _http_error("RATE_LIMITED", 429, status, True, details)
    if status in (500, 502, 503, 504):
        return _http_error("PROVIDER_UNAVAILABLE", 503, status, True, details)
    if status in NON_RETRYABLE_HTTP_STATUSES:
        return _http_error("LLM_INVALID_RESPONSE", 502, status, False, details)
    return _http_error(
        "PROVIDER_UNAVAILABLE", 503, status,
        status in RETRYABLE_HTTP_STATUSES, details,
    )


async def send_once(
    url: str,
    model: str,
    api_key: str,
    payload: dict,
    *,
    timeout: float,
    output_token_param: str,
    allowed_tools: FrozenSet[str] = frozenset(),
) -> LLMResult:
    """Ejecuta una única petición; no reintenta ni ejecuta herramientas.

    Args:
        allowed_tools: Allowlist inmutable del ciclo LLM actual para propagar al parser.
    """
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": "Bearer " + api_key},
            )
            if response.status_code >= 400:
                raise _map_http_status(
                    response.status_code,
                    parse_retry_after(response.headers.get("retry-after")),
                )
            body = response.json()
        return parse_response(
            body, payload, model, output_token_param,
            (time.monotonic() - started) * 1000,
            allowed_tools,
        )
    except httpx.TimeoutException as error:
        emit_event("warning", "llm_provider_error", model=model, cause="timeout")
        raise AgentError(
            "PROVIDER_TIMEOUT", "LLM provider timed out", 504, True,
            details={"origin": "timeout"},
        ) from error
    except AgentError:
        raise
    except httpx.RequestError as error:
        emit_event("warning", "llm_provider_error", model=model, cause="connection")
        raise AgentError(
            "PROVIDER_UNAVAILABLE", "LLM provider is unavailable", 503, True,
            details={"origin": "connection"},
        ) from error
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        emit_event("warning", "llm_provider_error", model=model, cause="invalid_response")
        raise AgentError(
            "LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502, False,
            details={"reason": type(error).__name__},
        ) from error
