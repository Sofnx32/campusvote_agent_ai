"""Transporte HTTP de una sola llamada al proveedor (sin reintentos)."""

from __future__ import annotations

import json
import re
import time
from typing import FrozenSet

import httpx

from ..errors import AgentError
from ..models import LLMResult
from ..forensic import (
    log_llm_invalid_response,
    log_llm_request_started,
    log_llm_request_completed,
    log_llm_request_failed,
)
from .response_parser import parse_response
from .retry_policy import NON_RETRYABLE_HTTP_STATUSES, RETRYABLE_HTTP_STATUSES, parse_retry_after
from .telemetry import emit_event


_UPSTREAM_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_UPSTREAM_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|token|secret|password|passwd|credential)\b\s*[:=]\s*[\"']?[^\s,;\"'}]+"
)


def _sanitize_upstream_text(value: object, limit: int = 500) -> str:
    """Texto acotado y seguro para `details.upstream_error`.

    `details` se expone al cliente, así que se eliminan UUIDs y posibles
    secretos antes de incluir el mensaje crudo del proveedor.
    """
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = _UPSTREAM_UUID_RE.sub("[uuid]", text)
    text = _UPSTREAM_SECRET_RE.sub(r"\1=[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _extract_upstream_error(response: object, limit: int = 500) -> str:
    """Extrae el primer mensaje útil del body de una respuesta 4xx."""
    body: object = None
    try:
        body = response.json()  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        body = None

    if isinstance(body, dict):
        for key in ("message", "msg", "detail", "error", "error_message"):
            if key not in body:
                continue
            value = body[key]
            if isinstance(value, dict):
                for nested in ("message", "msg", "detail", "type", "code"):
                    if nested in value:
                        return _sanitize_upstream_text(value[nested], limit)
                return _sanitize_upstream_text(value, limit)
            if value is not None:
                return _sanitize_upstream_text(value, limit)
        if body:
            return _sanitize_upstream_text(body, limit)
    elif body is not None:
        return _sanitize_upstream_text(body, limit)

    text = getattr(response, "text", "")
    if isinstance(text, str) and text.strip():
        return _sanitize_upstream_text(text, limit)
    return ""


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


def _map_http_status(
    status: int,
    retry_after: float | None,
    allowed_tools: FrozenSet[str] | None = None,
    upstream_error: str = "",
) -> AgentError:
    details = {"retry_after_seconds": retry_after} if retry_after is not None else {}
    if status in (400, 422):
        details["upstream_error"] = upstream_error or "<sin cuerpo de error>"
    if status in (401, 403):
        return _http_error("PROVIDER_AUTH_FAILED", 502, status, False, details)
    if status == 404:
        return _http_error("LLM_MODEL_NOT_FOUND", 502, status, False, details)
    if status == 429:
        return _http_error("RATE_LIMITED", 429, status, True, details)
    if status in (500, 502, 503, 504):
        return _http_error("PROVIDER_UNAVAILABLE", 503, status, True, details)
    if status in NON_RETRYABLE_HTTP_STATUSES:
        log_llm_invalid_response(
            "finish_reason",
            selected_tool_names=sorted(allowed_tools or ()),
            origin_status=status,
            detail="non_retryable_http_status",
            upstream_error=details.get("upstream_error"),
        )
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
                    allowed_tools,
                    _extract_upstream_error(response),
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
        log_llm_invalid_response(
            "invalid_tool_json",
            selected_tool_names=sorted(allowed_tools),
            detail=type(error).__name__,
        )
        raise AgentError(
            "LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502, False,
            details={"reason": type(error).__name__},
        ) from error
