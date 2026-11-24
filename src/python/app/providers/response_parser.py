"""Parseo de respuestas OpenAI-compatible: tool calls y límite de salida.

El parser valida los tool_calls contra el registro canónico (TOOLS) y propaga
la allowlist inmutable del ciclo LLM actual para validación pre-ejecución.

Los modelos OpenAI-compatible suelen devolver ``arguments`` como string JSON.
Cuando el modelo envuelve ese JSON en bloques markdown (```json ... ```) o
añade prosa antes/después, el JSON sigue siendo recuperable: se extrae el
objeto balanceado y se registra la recuperación antes de ejecutar la herramienta.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import FrozenSet

from ..errors import AgentError
from ..models import LLMResult, ToolCall
from ..observability import current_metrics
from ..forensic import log_llm_invalid_response, log_tool_validation_rejected
from .telemetry import emit_event
from .tool_registry import TOOLS


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_UNCLOSED_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*)$", re.IGNORECASE | re.DOTALL)
_MAX_NESTED_DEPTH = 4


def _balanced_spans(text: str, opener: str, closer: str) -> list[str]:
    """Devuelve los fragmentos balanceados de ``opener``/``closer`` del texto.

    Respeta comillas y escapes para no confundir llaves dentro de cadenas con
    llaves estructurales.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == opener:
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == closer and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start:index + 1])
                start = -1
    return spans


def _json_candidates(raw: str) -> list[tuple[str, str]]:
    """Estrategias ordenadas para extraer JSON de una respuesta con ruido."""
    text = raw.strip()
    candidates: list[tuple[str, str]] = [("direct", text)]
    seen = {text}

    def add(strategy: str, value: str) -> None:
        candidate = value.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append((strategy, candidate))

    for index, match in enumerate(_FENCE_RE.finditer(text), start=1):
        add(f"fence:{index}", match.group(1))
    unclosed = _UNCLOSED_FENCE_RE.match(text)
    if unclosed and "```" not in unclosed.group(1):
        add("unclosed_fence", unclosed.group(1))

    for index, span in enumerate(_balanced_spans(text, "{", "}"), start=1):
        add(f"balanced_object:{index}", span)
    for index, span in enumerate(_balanced_spans(text, "[", "]"), start=1):
        add(f"balanced_array:{index}", span)
    if "{" in text and "}" in text:
        add("outer_braces", text[text.index("{"):text.rindex("}") + 1])
    if "[" in text and "]" in text:
        add("outer_brackets", text[text.index("["):text.rindex("]") + 1])
    return candidates


def _excerpt(raw: object, limit: int = 240) -> str:
    """Versión acotada y de una sola línea para logs forenses."""
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(raw)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _invalid_arguments_error(
    raw: object,
    name: str,
    model: str,
    allowed_tools: FrozenSet[str],
    *,
    failure: str,
    json_error: str | None = None,
    extraction: str | None = None,
) -> AgentError:
    """Loguea y construye un error con el motivo exacto del fallo."""
    details: dict[str, object] = {
        "reason": "INVALID_TOOL_ARGUMENTS_JSON",
        "failure": failure,
        "tool": name,
    }
    if json_error:
        details["json_error"] = json_error
    if extraction:
        details["extraction"] = extraction
    log_llm_invalid_response(
        "invalid_tool_json",
        selected_tool_names=sorted(allowed_tools),
        error_code="AI_RESPONSE_INVALID",
        tool=name,
        model=model,
        excerpt=_excerpt(raw),
        detail_reason=details["reason"],
        failure=details["failure"],
        json_error=details.get("json_error"),
        extraction=details.get("extraction"),
    )
    return AgentError(
        "AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502,
        details=details,
    )


def _coerce_nested_json(value: object, depth: int = 0) -> tuple[object, bool]:
    """Convierte strings que contienen JSON contenedor en dict/list.

    Cubre el caso frecuente en que el modelo sanea el objeto principal pero
    deja ``criteria`` u otro campo complejo como texto con markdown.
    """
    if depth > _MAX_NESTED_DEPTH:
        return value, False
    if isinstance(value, dict):
        changed = False
        result: dict[str, object] = {}
        for key, item in value.items():
            coerced, sub_changed = _coerce_nested_json(item, depth + 1)
            result[key] = coerced
            changed = changed or sub_changed
        return result, changed
    if isinstance(value, list):
        changed = False
        result_list: list[object] = []
        for item in value:
            coerced, sub_changed = _coerce_nested_json(item, depth + 1)
            result_list.append(coerced)
            changed = changed or sub_changed
        return result_list, changed
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[`":
            for _, candidate in _json_candidates(stripped):
                try:
                    decoded = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, (dict, list)):
                    coerced, _ = _coerce_nested_json(decoded, depth + 1)
                    return coerced, True
    return value, False


def _parse_arguments(
    raw: object,
    name: str,
    model: str,
    allowed_tools: FrozenSet[str],
) -> dict:
    """Normaliza ``arguments`` a dict o lanza un error con causa verificable."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        arguments, changed = _coerce_nested_json(raw)
        if changed:
            log_llm_invalid_response(
                "invalid_tool_json",
                selected_tool_names=sorted(allowed_tools),
                error_code="AI_RESPONSE_INVALID",
                outcome="recovered",
                tool=name,
                model=model,
                failure="nested_json_string",
                extraction="nested_object",
                excerpt=_excerpt(raw),
            )
        return arguments if isinstance(arguments, dict) else {}
    if not isinstance(raw, str):
        raise _invalid_arguments_error(
            raw, name, model, allowed_tools,
            failure=f"not_object:{type(raw).__name__}",
            json_error=f"native {type(raw).__name__}; expected object",
        )

    text = raw.strip()
    if not text:
        return {}

    first_error: str | None = None
    first_parsed_type: str | None = None
    decoded: dict | None = None
    used_strategy: str | None = None
    attempted: list[str] = []

    for strategy, candidate in _json_candidates(text):
        attempted.append(strategy)
        try:
            candidate_value = json.loads(candidate)
        except json.JSONDecodeError as error:
            if first_error is None:
                first_error = (
                    f"{strategy}: {error.msg} "
                    f"(line {error.lineno} column {error.colno})"
                )
            continue
        if isinstance(candidate_value, dict):
            decoded = candidate_value
            used_strategy = strategy
            break
        if first_parsed_type is None:
            first_parsed_type = type(candidate_value).__name__

    if decoded is None:
        failure = (
            f"not_object:{first_parsed_type}"
            if first_parsed_type else "json_decode_error"
        )
        raise _invalid_arguments_error(
            raw, name, model, allowed_tools,
            failure=failure,
            json_error=first_error,
            extraction=",".join(attempted),
        )

    arguments, nested_changed = _coerce_nested_json(decoded)
    if used_strategy != "direct" or nested_changed:
        log_llm_invalid_response(
            "invalid_tool_json",
            selected_tool_names=sorted(allowed_tools),
            error_code="AI_RESPONSE_INVALID",
            outcome="recovered",
            tool=name,
            model=model,
            failure=(
                "markdown_or_prose_cleaned"
                if used_strategy != "direct" else "nested_json_string"
            ),
            json_error=first_error,
            extraction=used_strategy or "nested_object",
            excerpt=_excerpt(raw),
        )
    return arguments if isinstance(arguments, dict) else {}


def _tool_calls_from(
    raw_calls: object,
    model: str,
    allowed_tools: FrozenSet[str],
) -> list[ToolCall]:
    """Parsea y valida SIEMPRE los ``tool_calls`` recibidos del proveedor.

    Que no se hayan anunciado schemas (conversación general, ``allow_tools=False``
    o fallback) no autoriza a descartar llamadas: deben validarse contra ``TOOLS``
    y producir un error funcional explícito si son inválidas.

    Cada ToolCall recibe la allowlist inmutable del ciclo para validación
    pre-ejecución centralizada sin recálculos ni estado global mutable.
    """
    from ..forensic import log_tool_validation_rejected
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
            # Forensic: herramienta desconocida en respuesta LLM
            log_tool_validation_rejected(
                validation_error_code="TOOL_NOT_FOUND",
                tool_name=name,
                tool_call_index=0,
                allowed_tools=sorted(allowed_tools) if allowed_tools else [],
            )
            raise AgentError("AI_TOOL_NOT_FOUND", "LLM requested an unknown tool", 502)
        arguments = _parse_arguments(
            function.get("arguments"), name, model, allowed_tools,
        )
        parsed.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{uuid.uuid4().hex}"),
                name=name,
                arguments=arguments,
                allowed_tools=allowed_tools,
            )
        )
    return parsed


def parse_response(
    body: object,
    payload: dict,
    model: str,
    output_token_param: str,
    latency_ms: float,
    allowed_tools: FrozenSet[str] = frozenset(),
) -> LLMResult:
    """Convierte el body del proveedor en LLMResult o error controlado.

    Args:
        allowed_tools: Allowlist inmutable de herramientas anunciadas en ESTE ciclo LLM.
            Se propaga a cada ToolCall para validación pre-ejecución.
    """
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
        log_llm_invalid_response(
            "finish_reason",
            selected_tool_names=sorted(allowed_tools),
            error_code="LLM_OUTPUT_TOKEN_LIMIT",
            model=model,
            finish_reason="length",
        )
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
    tool_calls = _tool_calls_from(message.get("tool_calls"), model, allowed_tools)
    if tool_calls:
        result = LLMResult(content=content or None, tool_calls=tool_calls, allowed_tools=allowed_tools)
    elif content.strip():
        result = LLMResult(content=content, allowed_tools=allowed_tools)
    else:
        reason = "empty_content_after_tools" if allowed_tools else "empty_content_no_tools"
        log_llm_invalid_response(
            reason,
            selected_tool_names=sorted(allowed_tools),
            error_code="AI_RESPONSE_INVALID",
            model=model,
            finish_reason=choice.get("finish_reason"),
        )
        raise AgentError(
            "AI_RESPONSE_INVALID", "LLM returned no content", 502,
            details={"reason": "EMPTY_CONTENT", "finish_reason": choice.get("finish_reason")},
        )

    result.usage = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    result.latency_ms = latency_ms
    return result
