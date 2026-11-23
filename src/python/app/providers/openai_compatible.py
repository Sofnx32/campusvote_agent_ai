"""Proveedor OpenAI-compatible: orquestación de intentos y fallback.

La lógica se divide en módulos de responsabilidad única:
  - output_tokens: parámetro de límite de salida.
  - retry_policy: estados transitorios, backoff y presupuesto.
  - http_transport: una llamada HTTP sin reintentos.
  - response_parser: parseo de choices, tools y finish_reason.
  - telemetry: logs JSON correlacionados y sanitizados.
"""

from __future__ import annotations

import time
from typing import FrozenSet

from ..config import settings
from ..errors import AgentError
from ..models import LLMResult
from ..observability import current_metrics, request_id_context
from ..forensic import (
    log_llm_request_started,
    log_llm_request_completed,
    log_llm_request_failed,
)
from ..prompts import system_prompt
from .base import LLMProvider
from .http_transport import send_once
from .output_tokens import output_token_param as resolve_output_token_param
from .retry_policy import (
    FALLBACK_ERROR_CODES,
    calculate_backoff,
    is_retryable,
    sleep_with_budget_check,
    total_attempts,
)
from .telemetry import emit_attempt_log, emit_event
from .tool_selector import select_tool_names
from .tool_registry import READ_TOOLS, TOOLS, WRITE_TOOLS  # noqa: F401 (API pública)
from ..tool_schemas import tool_schema  # noqa: F401 (API pública)


def fallback_endpoint() -> tuple[str, str, str] | None:
    url = (getattr(settings, "llm_fallback_url", "") or "").strip()
    model = (getattr(settings, "llm_fallback_model", "") or "").strip()
    api_key = (getattr(settings, "llm_fallback_api_key", "") or "").strip()
    return (url, model, api_key) if url and model and api_key else None


class OpenAICompatibleProvider(LLMProvider):
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        if not settings.llm_provider or not settings.llm_url or not settings.llm_model or not settings.llm_api_key:
            raise AgentError("LLM_CONFIG_MISSING", "LLM provider configuration is incomplete", 503)

        param = resolve_output_token_param(settings.llm_output_token_param, settings.llm_model)
        payload: dict = {
            "model": settings.llm_model,
            "messages": [{"role": "system", "content": system_prompt(context)}, *messages],
        }
        tool_names: list[str] = []
        if allow_tools:
            tool_names = select_tool_names(context, messages, allow_tools)
            if tool_names:
                payload["tools"] = [
                    {"type": "function", "function": tool_schema(name)} for name in tool_names
                ]
                payload["tool_choice"] = "auto"
        if settings.llm_max_output_tokens > 0:
            payload[param] = settings.llm_max_output_tokens

        if settings.llm_total_budget_seconds <= settings.llm_fallback_budget_seconds:
            emit_event(
                "warning", "llm_budget_invalid",
                total_budget_seconds=settings.llm_total_budget_seconds,
                fallback_budget_seconds=settings.llm_fallback_budget_seconds,
            )

        budget_ends = time.monotonic() + settings.llm_total_budget_seconds
        allowed = frozenset(tool_names) if tool_names else frozenset()
        try:
            return await self._attempts(
                settings.llm_url, settings.llm_model, settings.llm_api_key, payload,
                timeout=min(settings.llm_timeout_seconds, settings.llm_total_budget_seconds),
                budget_ends=budget_ends, is_fallback=False, token_param=param,
                allowed_tools=allowed,
            )
        except AgentError as error:
            return await self._with_fallback(
                error, payload, param, budget_ends, allowed_tools=allowed,
            )

    async def _with_fallback(
        self,
        error: AgentError,
        payload: dict,
        token_param: str,
        primary_budget_ends: float,
        allowed_tools: FrozenSet[str],
    ) -> LLMResult:
        fallback = fallback_endpoint()
        if fallback is None:
            emit_event(
                "warning", "llm_fallback_not_configured",
                primary_model=settings.llm_model, cause=error.code,
                output_token_param=token_param,
            )
            raise error
        if error.code not in FALLBACK_ERROR_CODES:
            emit_event("warning", "llm_fallback_not_applicable", primary_model=settings.llm_model, cause=error.code)
            raise error

        metrics = current_metrics()
        if metrics:
            metrics.fallbacks += 1
        remaining = primary_budget_ends - time.monotonic()
        reserve = settings.llm_fallback_budget_seconds + settings.llm_retry_min_next_attempt_seconds
        if remaining < reserve:
            emit_event(
                "warning", "llm_fallback_skipped_insufficient_budget",
                primary_model=settings.llm_model,
                remaining_seconds=round(remaining, 2),
                fallback_budget_seconds=settings.llm_fallback_budget_seconds,
            )
            raise error

        url, model, api_key = fallback
        emit_event(
            "warning", "llm_fallback_engaged",
            primary_model=settings.llm_model, fallback_model=model, cause=error.code,
            remaining_seconds=round(remaining, 2),
            fallback_budget_seconds=settings.llm_fallback_budget_seconds,
        )
        # Fallback usa la misma allowlist (heredada del ciclo primario)
        return await self._attempts(
            url, model, api_key, {**payload, "model": model},
            timeout=min(settings.llm_timeout_seconds, settings.llm_fallback_budget_seconds),
            budget_ends=time.monotonic() + settings.llm_fallback_budget_seconds,
            is_fallback=True, token_param=token_param,
            allowed_tools=allowed_tools,
        )

    async def _attempts(
        self,
        url: str,
        model: str,
        api_key: str,
        payload: dict,
        *,
        timeout: float,
        budget_ends: float,
        is_fallback: bool,
        token_param: str,
        allowed_tools: FrozenSet[str],
    ) -> LLMResult:
        """Reintentos acotados al cliente Python; nunca re-ejecuta tools.

        La allowlist inmutable se propaga a send_once -> parse_response.
        """
        request_id = request_id_context.get()
        phase = "fallback" if is_fallback else "primary"
        metrics = current_metrics()
        attempts = total_attempts(settings.llm_max_retries)

        for attempt in range(1, attempts + 1):
            attempt_started = time.monotonic()
            if metrics:
                metrics.llm_calls += 1
            
            # Forensic: LLM request started
            log_llm_request_started(
                model=model,
                is_fallback=is_fallback,
                attempt=attempt,
                allowed_tool_count=len(allowed_tools) if allowed_tools else 0,
            )
            
            try:
                result = await send_once(
                    url, model, api_key, payload,
                    timeout=max(0.1, min(timeout, budget_ends - time.monotonic())),
                    output_token_param=token_param,
                    allowed_tools=allowed_tools,
                )
                latency = (time.monotonic() - attempt_started) * 1000
                if metrics:
                    metrics.provider_latency_ms += latency
                    metrics.record_usage(result.usage)
                emit_attempt_log(
                    request_id=request_id, phase=phase, model=model, attempt=attempt,
                    max_retries=settings.llm_max_retries, latency_ms=latency,
                    is_fallback=is_fallback, usage=result.usage or None,
                )
                # Forensic: LLM request completed
                log_llm_request_completed(
                    model=model,
                    is_fallback=is_fallback,
                    attempt=attempt,
                    duration_ms=latency,
                    response_tool_call_count=len(result.tool_calls) if result.tool_calls else 0,
                    response_has_tool_calls=bool(result.tool_calls),
                    prompt_tokens=result.usage.get("prompt_tokens", 0) if result.usage else 0,
                    completion_tokens=result.usage.get("completion_tokens", 0) if result.usage else 0,
                    total_tokens=result.usage.get("total_tokens", 0) if result.usage else 0,
                )
                return result
            except AgentError as error:
                latency = (time.monotonic() - attempt_started) * 1000
                if metrics:
                    metrics.provider_latency_ms += latency
                    if error.code == "RATE_LIMITED":
                        metrics.rate_limited_429 += 1
                    if error.code == "PROVIDER_TIMEOUT":
                        metrics.timeouts += 1
                details = error.details if isinstance(error.details, dict) else {}
                emit_attempt_log(
                    request_id=request_id, phase=phase, model=model, attempt=attempt,
                    max_retries=settings.llm_max_retries, latency_ms=latency,
                    is_fallback=is_fallback, error_code=error.code,
                    origin_status=details.get("origin_status"),
                )
                # Forensic: LLM request failed
                log_llm_request_failed(
                    model=model,
                    is_fallback=is_fallback,
                    attempt=attempt,
                    duration_ms=latency,
                    provider_error_code=error.code,
                    retry_attempt=attempt,
                )
                if attempt >= attempts or not is_retryable(error):
                    raise
                wait = self._retry_wait(error, attempt, attempt + 1, attempts, budget_ends, metrics)
                await sleep_with_budget_check(wait, budget_ends)
        raise AgentError("PROVIDER_UNAVAILABLE", "All provider attempts exhausted", 503, True)

    def _retry_wait(
        self,
        error: AgentError,
        attempt: int,
        next_attempt: int,
        attempts: int,
        budget_ends: float,
        metrics,
    ) -> float:
        details = error.details if isinstance(error.details, dict) else {}
        wait = calculate_backoff(
            attempt - 1,
            settings.llm_retry_base_delay_seconds,
            settings.llm_retry_max_delay_seconds,
            details.get("retry_after_seconds"),
        )
        remaining = budget_ends - time.monotonic()
        reserve = wait + settings.llm_retry_min_next_attempt_seconds
        if wait >= remaining or reserve >= remaining:
            emit_event(
                "warning", "llm_retry_aborted_insufficient_budget",
                model=settings.llm_model, attempt=next_attempt, max_attempts=attempts,
                wait_seconds=round(wait, 2), remaining_seconds=round(remaining, 2),
                cause=error.code,
            )
            raise AgentError(
                "AI_DEADLINE_EXCEEDED", "Insufficient time budget for provider retry", 504, True,
                details={"wait_seconds": round(wait, 2), "remaining_seconds": round(remaining, 2), "cause": error.code},
            ) from error
        if metrics:
            metrics.retries += 1
        emit_event(
            "warning", "llm_retry_attempt", model=settings.llm_model,
            attempt=next_attempt, max_attempts=attempts,
            wait_seconds=round(wait, 2), cause=error.code,
        )
        return wait
