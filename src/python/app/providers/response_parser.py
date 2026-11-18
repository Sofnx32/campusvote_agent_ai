"""Parseo de respuestas OpenAI-compatible: tool calls y límite de salida."""

from __future__ import annotations

import json
import uuid

from ..errors import AgentError
from ..models import LLMResult, ToolCall
from ..observability import current_metrics
from .telemetry import emit_event
from .tool_registry import TOOLS


def _tool_calls_from(raw_calls: object, model: str) -> list[ToolCall]:
    """Parsea y valida SIEMPRE los ``tool_calls`` recibidos del proveedor.

    Que no se hayan anunciado schemas (conversación general, ``allow_tools=False``
    o fallback) no autoriza a descartar llamadas: deben validarse contra ``TOOLS``
    y producir un error funcional explícito si son inválidas.
    """
    if not raw_calls:
        return []
    if not isinstance(raw_calls, list):
        raise ValueError("tool_calls must be a list")
    parsed: list[ToolCall] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            raise ValueError("tool call must be an object")
        function = raw.get("function") or {}
        if not isinstance(function, dict):
            raise ValueError("tool function must be an object")
        name = function.get("name")
        if not isinstance(name, str) or name not in TOOLS:
            emit_event("warning", "llm_unknown_tool", model=model, tool=str(name)[:64])
            raise AgentError("AI_TOOL_NOT_FOUND", "LLM requested an unknown tool", 502)
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as error:
                raise AgentError(
                    "AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502,
                    details={"reason": "INVALID_TOOL_ARGUMENTS_JSON"},
                ) from error
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise AgentError("AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502)
        parsed.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{uuid.uuid4().hex}"),
                name=name,
                arguments=arguments,
            )
        )
    return parsed


def parse_response(
    body: object,
    payload: dict,
    model: str,
    output_token_param: str,
    latency_ms: float,
) -> LLMResult:
    """Convierte el body del proveedor en LLMResult o error controlado."""
    if not isinstance(body, dict):
        raise ValueError("provider body must be an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("provider returned no choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("provider returned an invalid message")
    message = choice["message"]
    if "content" not in message and "tool_calls" not in message:
        raise ValueError("provider returned an invalid message")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}

    if choice.get("finish_reason") == "length":
        # Error explícito: evita devolver contenido o argumentos JSON truncados
        # como si fueran una respuesta completa.
        emit_event(
            "warning", "llm_output_truncated",
            model=model, finish_reason="length",
            output_token_param=output_token_param,
            output_token_limit=payload.get(output_token_param),
            usage=usage,
        )
        metrics = current_metrics()
        if metrics:
            metrics.truncated_responses += 1
        raise AgentError(
            "LLM_OUTPUT_TOKEN_LIMIT",
            "LLM output was truncated by the token limit",
            502,
            False,
            details={
                "finish_reason": "length",
                "output_token_param": output_token_param,
                "output_token_limit": payload.get(output_token_param),
            },
        )

    content = message.get("content")
    if not isinstance(content, str):
        content = ""
    tool_calls = _tool_calls_from(message.get("tool_calls"), model)
    if tool_calls:
        result = LLMResult(content=content or None, tool_calls=tool_calls)
    elif content.strip():
        result = LLMResult(content=content)
    else:
        raise AgentError("AI_RESPONSE_INVALID", "LLM returned no content", 502)

    result.usage = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    result.latency_ms = latency_ms
    return result
