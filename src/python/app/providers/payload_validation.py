"""Validación estricta del payload enviado al proveedor OpenAI-compatible.

Objetivo: detectar localmente las inconsistencias que los proveedores responden
con HTTP 400/422 antes de gastar un intento remoto:

- mezclar ``response_format`` con ``tools``;
- esquemas de funciones con tipos inválidos o arrays sin ``items``;
- mensajes con roles/content inválidos, ``tool_call_id`` huérfano o
  campos extra en mensajes ``tool``;
- ``assistant`` con ``tool_calls`` y ``content: ""`` en vez de ``null``.

La validación es conservadora: solo acepta la intersección estricta soportada
por la especificación de chat completions con function calling.
"""

from __future__ import annotations

import json

from ..errors import AgentError
from ..forensic import log_llm_invalid_response

_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
_JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})
_TOOL_CALL_KEYS = frozenset({"id", "type", "function"})
_FUNCTION_KEYS = frozenset({"name", "arguments"})
_TOOL_MESSAGE_KEYS = frozenset({"role", "content", "tool_call_id"})


def _fail(detail: str, tool_names: list[str] | None = None) -> None:
    log_llm_invalid_response(
        "invalid_provider_payload",
        selected_tool_names=tool_names or [],
        error_code="LLM_INVALID_RESPONSE",
        detail=detail,
    )
    raise AgentError(
        "LLM_INVALID_RESPONSE",
        "Provider payload does not match the strict chat schema",
        502,
        False,
        details={"reason": "INVALID_PROVIDER_PAYLOAD", "detail": detail},
    )


def _validate_schema(node: object, path: str, tool_names: list[str]) -> None:
    if not isinstance(node, dict):
        _fail(f"{path}: schema must be an object", tool_names)
    node_type = node.get("type")
    if node_type is not None and node_type not in _JSON_TYPES:
        _fail(f"{path}: invalid JSON type {node_type!r}", tool_names)
    if node_type == "array":
        items = node.get("items")
        if not isinstance(items, dict):
            _fail(f"{path}: array requires an object 'items' schema", tool_names)
        _validate_schema(items, f"{path}.items", tool_names)
    if node_type == "object":
        properties = node.get("properties", {})
        if not isinstance(properties, dict):
            _fail(f"{path}: object 'properties' must be an object", tool_names)
        required = node.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(item, str) or item not in properties for item in required
        ):
            _fail(f"{path}: 'required' must list only declared properties", tool_names)
        for key, value in properties.items():
            _validate_schema(value, f"{path}.properties.{key}", tool_names)


def _validate_tools(tools: object, tool_names: list[str]) -> None:
    if not isinstance(tools, list) or not tools:
        _fail("tools must be a non-empty array", tool_names)
    for index, tool in enumerate(tools):
        path = f"tools[{index}]"
        if not isinstance(tool, dict) or tool.get("type") != "function":
            _fail(f"{path}: expected type='function'", tool_names)
        function = tool.get("function")
        if not isinstance(function, dict):
            _fail(f"{path}.function must be an object", tool_names)
        name = function.get("name")
        if not isinstance(name, str) or not name:
            _fail(f"{path}.function.name must be a non-empty string", tool_names)
        if not isinstance(function.get("description"), str):
            _fail(f"{path}.function.description must be a string", tool_names)
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            _fail(f"{path}.function.parameters must be an object schema", tool_names)
        _validate_schema(parameters, f"{path}.function.parameters", tool_names)


def _validate_messages(messages: object, tool_names: list[str]) -> None:
    if not isinstance(messages, list) or not messages:
        _fail("messages must be a non-empty array", tool_names)
    first = messages[0]
    if not isinstance(first, dict) or first.get("role") != "system":
        _fail("messages[0] must be the system message", tool_names)
    if not isinstance(first.get("content"), str) or not first["content"].strip():
        _fail("messages[0].content must be a non-empty string", tool_names)

    declared_call_ids: set[str] = set()
    answered_call_ids: set[str] = set()
    for index, message in enumerate(messages):
        path = f"messages[{index}]"
        if not isinstance(message, dict):
            _fail(f"{path} must be an object", tool_names)
        role = message.get("role")
        if role not in _ALLOWED_ROLES:
            _fail(f"{path}.role {role!r} is not supported", tool_names)
        content = message.get("content")

        if role in ("system", "user"):
            if not isinstance(content, str) or not content.strip():
                _fail(f"{path}.content must be a non-empty string", tool_names)
            continue

        if role == "tool":
            if set(message) - _TOOL_MESSAGE_KEYS:
                _fail(f"{path}: tool messages only allow role/content/tool_call_id", tool_names)
            if not isinstance(content, str) or not content:
                _fail(f"{path}.content must be a non-empty string", tool_names)
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                _fail(f"{path}.tool_call_id must be a non-empty string", tool_names)
            if call_id in answered_call_ids:
                _fail(f"{path}.tool_call_id is duplicated", tool_names)
            answered_call_ids.add(call_id)
            previous = messages[index - 1] if index else None
            previous_calls = previous.get("tool_calls") if isinstance(previous, dict) else None
            if not isinstance(previous_calls, list) or not any(
                isinstance(call, dict) and call.get("id") == call_id
                for call in previous_calls
            ):
                _fail(f"{path}.tool_call_id has no preceding assistant tool_call", tool_names)
            continue

        calls = message.get("tool_calls") or []
        if not calls:
            if not isinstance(content, str) or not content.strip():
                _fail(f"{path}.content must be a non-empty string", tool_names)
            continue
        if not isinstance(calls, list):
            _fail(f"{path}.tool_calls must be an array", tool_names)
        if content == "":
            _fail(f"{path}.content must be null (not empty) when tool_calls are present", tool_names)
        if content is not None and not isinstance(content, str):
            _fail(f"{path}.content must be a string or null", tool_names)
        for call_index, call in enumerate(calls):
            call_path = f"{path}.tool_calls[{call_index}]"
            if not isinstance(call, dict) or set(call) - _TOOL_CALL_KEYS:
                _fail(f"{call_path}: invalid tool call shape", tool_names)
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id or call_id in declared_call_ids:
                _fail(f"{call_path}.id must be a unique non-empty string", tool_names)
            declared_call_ids.add(call_id)
            if call.get("type") != "function":
                _fail(f"{call_path}.type must be 'function'", tool_names)
            function = call.get("function")
            if not isinstance(function, dict) or set(function) - _FUNCTION_KEYS:
                _fail(f"{call_path}.function has an invalid shape", tool_names)
            if not isinstance(function.get("name"), str) or not function["name"]:
                _fail(f"{call_path}.function.name must be a non-empty string", tool_names)
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                _fail(f"{call_path}.function.arguments must be a JSON string", tool_names)
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                _fail(f"{call_path}.function.arguments is not valid JSON", tool_names)
            if not isinstance(parsed, dict):
                _fail(f"{call_path}.function.arguments must decode to an object", tool_names)


def validate_llm_payload(payload: object) -> None:
    """Valida el payload completo o lanza un `LLM_INVALID_RESPONSE` local."""
    if not isinstance(payload, dict):
        _fail("payload must be an object")
    tool_names = [
        tool.get("function", {}).get("name", "")
        for tool in payload.get("tools", [])
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    ]
    _validate_messages(payload.get("messages"), tool_names)

    has_tools = "tools" in payload
    if has_tools:
        if "response_format" in payload:
            _fail("response_format must not be combined with tools", tool_names)
        _validate_tools(payload.get("tools"), tool_names)
    elif "tool_choice" in payload:
        _fail("tool_choice requires tools to be announced")
