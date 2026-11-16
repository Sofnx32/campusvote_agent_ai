from __future__ import annotations

import json
import logging
import time
import uuid

import httpx

from ..config import settings
from ..errors import AgentError
from ..models import LLMResult, ToolCall
from ..observability import request_id_context
from ..prompts import system_prompt
from ..tool_schemas import tool_schema
from .base import LLMProvider

logger = logging.getLogger(__name__)

TOOLS = [
    "getSiteOverview", "getSiteFairs", "getSiteProjects", "getSiteStands", "getSiteResults",
    "listTeachingCriteria", "createTeachingCriterion", "updateTeachingCriterion",
    "toggleTeachingCriterion", "deleteTeachingCriterion",
    "listFairCategories", "listFairCategoryRubrics",
    "createFairRubric", "addFairRubricCriterion",
    "createCategoryFairRubric", "addCategoryFairRubricCriterion",
    "createFair", "updateFair", "changeFairStatus",
    "listFairStands", "createFairStand", "updateFairStand", "deleteFairStand",
]

READ_TOOLS = {
    "getSiteOverview", "getSiteFairs", "getSiteProjects", "getSiteStands", "getSiteResults",
    "listFairStands", "listFairCategories", "listFairCategoryRubrics", "listTeachingCriteria",
}
WRITE_TOOLS = set(TOOLS) - READ_TOOLS

FALLBACK_ERROR_CODES = frozenset({
    "LLM_AUTH_FAILED", "LLM_MODEL_NOT_FOUND", "LLM_RATE_LIMITED",
    "LLM_PROVIDER_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE",
})


def fallback_endpoint() -> tuple[str, str, str] | None:
    url = (getattr(settings, "llm_fallback_url", "") or "").strip()
    model = (getattr(settings, "llm_fallback_model", "") or "").strip()
    api_key = (getattr(settings, "llm_fallback_api_key", "") or "").strip()
    if url and model and api_key:
        return url, model, api_key
    return None


class OpenAICompatibleProvider(LLMProvider):
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        if not settings.llm_provider or not settings.llm_url or not settings.llm_model or not settings.llm_api_key:
            raise AgentError("LLM_CONFIG_MISSING", "LLM provider configuration is incomplete", 503)
        payload: dict = {
            "model": settings.llm_model,
            "messages": [{"role": "system", "content": system_prompt(context)}, *messages],
        }
        if allow_tools:
            payload["tools"] = [
                {"type": "function", "function": tool_schema(name)} for name in TOOLS
            ]
            payload["tool_choice"] = "auto"
        budget_ends = time.monotonic() + settings.llm_total_budget_seconds
        try:
            return await self._send(
                settings.llm_url, settings.llm_model, settings.llm_api_key, payload,
                timeout=min(settings.llm_timeout_seconds, settings.llm_total_budget_seconds),
            )
        except AgentError as error:
            fallback = fallback_endpoint()
            if fallback is None or error.code not in FALLBACK_ERROR_CODES:
                raise
            remaining = budget_ends - time.monotonic()
            if remaining < 1:
                raise
            url, model, api_key = fallback
            logger.warning(
                "llm_fallback_engaged requestId=%s primary_model=%s fallback_model=%s cause=%s",
                request_id_context.get(), settings.llm_model, model, error.code,
            )
            return await self._send(
                url, model, api_key, {**payload, "model": model},
                timeout=min(settings.llm_timeout_seconds, remaining),
            )

    async def _send(self, url: str, model: str, api_key: str, payload: dict, timeout: float | None = None) -> LLMResult:
        try:
            async with httpx.AsyncClient(timeout=timeout or settings.llm_timeout_seconds) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": "Bearer " + api_key},
                )
                if response.status_code >= 400:
                    status = response.status_code
                    if status in (401, 403):
                        code, public_status = "LLM_AUTH_FAILED", 502
                    elif status == 404:
                        code, public_status = "LLM_MODEL_NOT_FOUND", 502
                    elif status == 429:
                        code, public_status = "LLM_RATE_LIMITED", 429
                    else:
                        code, public_status = "LLM_PROVIDER_UNAVAILABLE", 503
                    logger.warning(
                        "LLM request failed requestId=%s provider=%s model=%s status=%s cause=http_status",
                        request_id_context.get(), settings.llm_provider, model, status,
                    )
                    raise AgentError(code, "LLM provider request failed", public_status, status == 429 or status >= 500)
                body = response.json()
                choice = body["choices"][0]["message"]
                if not isinstance(choice, dict):
                    raise ValueError("message must be an object")
        except httpx.TimeoutException as error:
            logger.warning("LLM request failed requestId=%s provider=%s model=%s cause=timeout", request_id_context.get(), settings.llm_provider, model)
            raise AgentError("LLM_PROVIDER_TIMEOUT", "LLM provider timed out", 504, True) from error
        except AgentError:
            raise
        except httpx.RequestError as error:
            logger.warning(
                "LLM request failed requestId=%s provider=%s model=%s cause=%s",
                request_id_context.get(), settings.llm_provider, model, type(error).__name__,
            )
            raise AgentError("LLM_PROVIDER_UNAVAILABLE", "LLM provider is unavailable", 503, True) from error
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning(
                "LLM response invalid requestId=%s provider=%s model=%s cause=%s",
                request_id_context.get(), settings.llm_provider, model, type(error).__name__,
            )
            raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502) from error

        content = choice.get("content")
        if not isinstance(content, str):
            content = ""
        raw_calls = choice.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502)
        tool_calls: list[ToolCall] = []
        if payload.get("tools"):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502)
                function = raw.get("function") or {}
                name = function.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502)
                if name not in TOOLS:
                    logger.warning(
                        "LLM requested unknown tool requestId=%s tool=%s",
                        request_id_context.get(), name,
                    )
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError as error:
                        raise AgentError("AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502) from error
                if arguments is None:
                    arguments = {}
                if not isinstance(arguments, dict):
                    raise AgentError("AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502)
                tool_calls.append(
                    ToolCall(
                        id=str(raw.get("id") or f"call_{uuid.uuid4().hex}"),
                        name=name,
                        arguments=arguments,
                    )
                )
        if tool_calls:
            return LLMResult(content=content or None, tool_calls=tool_calls)
        if not content.strip():
            raise AgentError("AI_RESPONSE_INVALID", "LLM returned no content", 502)
        return LLMResult(content=content)