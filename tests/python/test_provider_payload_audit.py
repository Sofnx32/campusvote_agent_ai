"""Auditoría del payload LLM: esquemas, mensajes y errores 400/422."""

from __future__ import annotations

import pytest

from provider_helpers import LogCapture, MockResponse, make_provider, run

from app.agent import _normalize_history
from app.errors import AgentError
from app.providers.payload_validation import validate_llm_payload
from app.providers.tool_registry import TOOLS
from app.tool_schemas import tool_schema

CONTEXT = {"site_id": "site-a"}
MESSAGES = [{"role": "user", "content": "crea una categoría"}]


def _walk_schema(node: dict) -> None:
    assert isinstance(node, dict)
    assert node.get("type") in {"object", "array", "string", "number", "integer", "boolean"}
    if node["type"] == "array":
        assert isinstance(node.get("items"), dict), "todo array debe declarar items"
        _walk_schema(node["items"])
    if node["type"] == "object":
        properties = node.get("properties", {})
        assert isinstance(properties, dict)
        assert node.get("additionalProperties") is False
        assert set(node.get("required", [])) <= set(properties)
        for child in properties.values():
            _walk_schema(child)


def test_every_announced_tool_has_a_valid_strict_schema():
    for name in TOOLS:
        function = tool_schema(name)
        assert function["name"] == name
        assert isinstance(function["description"], str) and function["description"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        _walk_schema(parameters)


def test_payload_never_mixes_response_format_with_tools(monkeypatch):
    captured = []
    Provider, _ = make_provider(monkeypatch, response=MockResponse(), captured=captured)
    run(Provider().complete(CONTEXT, MESSAGES))
    payload = captured[0]["json"]
    assert "response_format" not in payload
    assert "tools" in payload
    assert payload["tool_choice"] == "auto"
    assert "MODO RESPUESTA FINAL" not in payload["messages"][0]["content"]


def test_no_tools_cycle_forbids_tool_calls_in_the_system_prompt(monkeypatch):
    """El ciclo final deja claro al modelo que debe responder solo texto."""
    captured = []
    Provider, _ = make_provider(monkeypatch, response=MockResponse(), captured=captured)
    run(Provider().complete(CONTEXT, MESSAGES, allow_tools=False))
    payload = captured[0]["json"]
    system = payload["messages"][0]["content"]
    assert "tools" not in payload
    assert "NO debes invocar ninguna función ni herramienta" in system
    assert "Responde únicamente con texto" in system


def test_default_tool_budget_allows_a_read_then_a_write():
    """Un flujo lectura + escritura no debe agotar el presupuesto por defecto."""
    from app.config import settings as configured

    assert configured.max_tool_calls >= 5


def _valid_tool_payload():
    return {
        "model": "model-x",
        "messages": [
            {"role": "system", "content": "Sistema."},
            {"role": "user", "content": "Crea la categoría."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "getSiteFairs", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
        ],
        "tools": [{"type": "function", "function": tool_schema("getSiteFairs")}],
        "tool_choice": "auto",
    }


@pytest.mark.parametrize("mutate,expected", [
    (
        lambda payload: payload.update({"response_format": {"type": "json_object"}}),
        "response_format must not be combined with tools",
    ),
    (
        lambda payload: payload["messages"][3].update({"name": "getSiteFairs"}),
        "tool messages only allow role/content/tool_call_id",
    ),
    (
        lambda payload: payload["messages"][2].update({"content": ""}),
        "content must be null (not empty) when tool_calls are present",
    ),
    (
        lambda payload: payload["messages"][3].update({"tool_call_id": "call-otro"}),
        "tool_call_id has no preceding assistant tool_call",
    ),
])
def test_validator_rejects_non_strict_payloads(mutate, expected):
    payload = _valid_tool_payload()
    mutate(payload)
    with pytest.raises(AgentError) as error:
        validate_llm_payload(payload)
    assert error.value.details["reason"] == "INVALID_PROVIDER_PAYLOAD"
    assert expected in error.value.details["detail"]


def test_validator_accepts_the_strict_payload():
    validate_llm_payload(_valid_tool_payload())


def test_history_drops_orphan_tool_messages():
    history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "Procesando..."},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
        {"role": "user", "content": "crea la categoría"},
    ]
    assert _normalize_history(history) == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "Procesando..."},
        {"role": "user", "content": "crea la categoría"},
    ]


def test_400_error_exposes_sanitized_upstream_message(monkeypatch):
    raw = (
        "Invalid tools for request 577e0c15-c215-44da-9101-04fdcb66aa1a "
        "api_key=super-secreto-local"
    )
    with LogCapture("campusvote.forensic") as logs:
        Provider, AgentError = make_provider(
            monkeypatch,
            response=MockResponse(status=400, payload={"error": {"message": raw}}),
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    details = error.value.details
    assert details["origin_status"] == 400
    assert details["upstream_error"].startswith("Invalid tools for request [uuid]")
    assert "api_key=[redacted]" in details["upstream_error"]
    assert "super-secreto-local" not in details["upstream_error"]
    assert "577e0c15" not in details["upstream_error"]
    events = logs.events("llm_invalid_response")
    assert events[0]["upstream_error"] == details["upstream_error"]


def test_422_upstream_error_is_truncated_to_500_chars(monkeypatch):
    long_message = "x" * 800
    Provider, AgentError = make_provider(
        monkeypatch,
        response=MockResponse(status=422, payload={"message": long_message}),
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.details["origin_status"] == 422
    assert len(error.value.details["upstream_error"]) == 500
