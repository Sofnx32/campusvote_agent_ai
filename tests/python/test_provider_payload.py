"""Casos 1-4: límite de tokens, parámetro compatible y truncamiento visible."""

from __future__ import annotations

import pytest

from app.providers.tool_registry import TOOLS
from app.providers.tool_selector import select_tool_names

from provider_helpers import (
    LogCapture, MockResponse, content_response, make_provider, run, tool_response,
)

CONTEXT = {"site_id": "site-a"}
MESSAGES = [{"role": "user", "content": "hola"}]


def test_default_output_token_limit_is_2000_max_tokens(monkeypatch):
    captured = []
    Provider, _ = make_provider(monkeypatch, response=content_response(), captured=captured)
    run(Provider().complete(CONTEXT, MESSAGES))
    assert captured[0]["json"]["max_tokens"] == 2000
    assert "max_completion_tokens" not in captured[0]["json"]


def test_output_token_param_is_configurable(monkeypatch):
    captured = []
    Provider, _ = make_provider(
        monkeypatch, response=content_response(), captured=captured,
        overrides={"llm_output_token_param": "max_completion_tokens", "llm_max_output_tokens": 1500},
    )
    run(Provider().complete(CONTEXT, MESSAGES))
    assert captured[0]["json"]["max_completion_tokens"] == 1500
    assert "max_tokens" not in captured[0]["json"]


@pytest.mark.parametrize("model,expected", [
    ("gpt-5-mini", "max_completion_tokens"),
    ("o3-mini", "max_completion_tokens"),
    ("gemini-2.5-flash", "max_tokens"),
    ("model-x", "max_tokens"),
])
def test_auto_output_token_param_matches_model(monkeypatch, model, expected):
    captured = []
    Provider, _ = make_provider(
        monkeypatch, response=content_response(), captured=captured,
        overrides={"llm_output_token_param": "auto", "llm_model": model},
    )
    run(Provider().complete(CONTEXT, MESSAGES))
    assert expected in captured[0]["json"]


def test_finish_reason_length_is_logged_and_raises(monkeypatch):
    with LogCapture("app.providers.telemetry") as logs:
        Provider, AgentError = make_provider(
            monkeypatch,
            response=content_response("respuesta truncada", finish_reason="length"),
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "LLM_OUTPUT_TOKEN_LIMIT"
    events = logs.events("llm_output_truncated")
    assert len(events) == 1
    assert events[0]["finish_reason"] == "length"
    assert events[0]["output_token_param"] == "max_tokens"
    assert events[0]["output_token_limit"] == 2000
    assert "output_token_limit" in error.value.details


def test_usage_is_exposed_in_result(monkeypatch):
    usage = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    Provider, _ = make_provider(monkeypatch, response=content_response("hola", usage=usage))
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert result.content == "hola"
    assert result.usage == usage
    assert result.latency_ms >= 0


def test_multiple_tool_calls_are_preserved(monkeypatch):
    Provider, _ = make_provider(monkeypatch, response=MockPayload())
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert {call.name for call in result.tool_calls} == {"getSiteFairs", "getSiteProjects"}


class MockPayload:
    """Dos tool calls en un solo turno."""

    def __init__(self):
        self.status_code = 200
        self.headers = {}
        self.payload = {
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "1", "function": {"name": "getSiteFairs", "arguments": "{}"}},
                {"id": "2", "function": {"name": "getSiteProjects", "arguments": "{}"}},
            ]}}],
        }

    def json(self):
        return self.payload


def test_unknown_tool_is_rejected(monkeypatch):
    Provider, AgentError = make_provider(monkeypatch, response=tool_response("unknownTool"))
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "AI_TOOL_NOT_FOUND"


def test_invalid_tool_arguments_are_not_silent(monkeypatch):
    response = MockResponse(payload={
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "function": {"name": "createFair", "arguments": '{"name": "feria"'}},
        ]}}],
    })
    Provider, AgentError = make_provider(monkeypatch, response=response)
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "AI_RESPONSE_INVALID"
    assert error.value.details["reason"] == "INVALID_TOOL_ARGUMENTS_JSON"


@pytest.mark.parametrize("payload", [
    {"choices": []},
    {"choices": [{"message": {}}]},
    "not a dict",
])
def test_invalid_provider_responses_are_classified(monkeypatch, payload):
    Provider, AgentError = make_provider(monkeypatch, response=MockResponse(payload=payload))
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "LLM_INVALID_RESPONSE"


def test_tools_disabled_returns_text_only(monkeypatch):
    Provider, _ = make_provider(monkeypatch, response=content_response("texto sin herramientas"))
    result = run(Provider().complete(CONTEXT, MESSAGES, allow_tools=False))
    assert result.tool_calls == []


# --- Regresiones Etapa 3.2: protocolo de tool_calls vs. omisión de schemas ---


def test_general_conversation_omits_tool_schemas(monkeypatch):
    """1) 'hola' -> selector [] y proveedor sin schemas (optimización preservada)."""
    captured = []
    assert select_tool_names({}, MESSAGES) == []
    Provider, _ = make_provider(
        monkeypatch, response=content_response("hola"), captured=captured,
    )
    run(Provider().complete(CONTEXT, MESSAGES))
    assert "tools" not in captured[0]["json"]
    assert "tool_choice" not in captured[0]["json"]


def test_text_response_without_tool_calls_is_unchanged(monkeypatch):
    """2) Respuesta sin tool_calls: comportamiento normal preservado."""
    Provider, _ = make_provider(monkeypatch, response=content_response("respuesta normal"))
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert result.content == "respuesta normal"
    assert result.tool_calls == []


def test_unexpected_tool_call_is_not_silently_discarded(monkeypatch):
    """3) tool_call inesperado (sin schemas anunciados) se parsea y valida."""
    Provider, _ = make_provider(
        monkeypatch, response=tool_response("getSiteFairs", {"limit": 1}),
    )
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert [call.name for call in result.tool_calls] == ["getSiteFairs"]
    assert result.tool_calls[0].arguments == {"limit": 1}


def test_unknown_tool_rejected_even_when_subset_is_announced(monkeypatch):
    """4) Nombre desconocido -> AI_TOOL_NOT_FOUND, aunque haya subconjunto anunciado."""
    captured = []
    Provider, AgentError = make_provider(
        monkeypatch, response=tool_response("toolInexistente", {}), captured=captured,
    )
    messages = [{"role": "user", "content": "Muéstrame las ferias"}]
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, messages))
    assert error.value.code == "AI_TOOL_NOT_FOUND"
    announced = [schema["function"]["name"] for schema in captured[0]["json"]["tools"]]
    assert announced and set(announced) <= set(TOOLS)


def test_invalid_arguments_are_structured_without_silent_fallback(monkeypatch):
    """5) JSON inválido -> error estructurado, sin TypeError secundario."""
    response = MockResponse(payload={"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "x1", "function": {"name": "getSiteFairs", "arguments": "{no-json"}},
    ]}}]})
    Provider, AgentError = make_provider(monkeypatch, response=response)
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "AI_RESPONSE_INVALID"
    assert error.value.details is not None
    assert error.value.details["reason"] == "INVALID_TOOL_ARGUMENTS_JSON"


def test_all_tool_calls_are_preserved_with_ids_and_order(monkeypatch):
    """6 y 10) Todas las llamadas preservadas, sin duplicados en el protocolo."""
    payload = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "a", "function": {"name": "getSiteFairs", "arguments": "{}"}},
        {"id": "b", "function": {"name": "getSiteProjects", "arguments": "{}"}},
        {"id": "c", "function": {"name": "getSiteResults", "arguments": "{}"}},
    ]}}]}
    Provider, _ = make_provider(monkeypatch, response=MockResponse(payload=payload))
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert [call.id for call in result.tool_calls] == ["a", "b", "c"]
    assert [call.name for call in result.tool_calls] == [
        "getSiteFairs", "getSiteProjects", "getSiteResults",
    ]
    assistant = result.assistant_message()
    assert [call["id"] for call in assistant["tool_calls"]] == ["a", "b", "c"]
    assert len(assistant["tool_calls"]) == len(result.tool_calls)


def test_subset_announced_and_registered_tool_call_parsed(monkeypatch):
    """8) El selector anuncia solo el dominio y la llamada registrada se parsea."""
    captured = []
    Provider, _ = make_provider(
        monkeypatch, response=tool_response("getSiteFairs", {}), captured=captured,
    )
    messages = [{"role": "user", "content": "Muéstrame las ferias"}]
    result = run(Provider().complete(CONTEXT, messages))
    announced = [schema["function"]["name"] for schema in captured[0]["json"]["tools"]]
    assert 0 < len(announced) < len(TOOLS)
    assert set(announced) <= set(TOOLS)
    assert [call.name for call in result.tool_calls] == ["getSiteFairs"]


def test_tools_disabled_announces_no_schemas(monkeypatch):
    """9) allow_tools=False no anuncia schemas ni devuelve tool_calls."""
    captured = []
    Provider, _ = make_provider(
        monkeypatch, response=content_response("solo texto"), captured=captured,
    )
    result = run(Provider().complete(CONTEXT, MESSAGES, allow_tools=False))
    assert "tools" not in captured[0]["json"]
    assert "tool_choice" not in captured[0]["json"]
    assert result.tool_calls == []


def test_write_tool_call_is_parsed_once_without_provider_retry(monkeypatch):
    """10) Una llamada de escritura se parsea una vez; sin ejecución duplicada."""
    captured = []
    Provider, _ = make_provider(
        monkeypatch,
        response=tool_response("createFair", {"name": "Feria de ciencias"}),
        captured=captured,
    )
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert len(captured) == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "createFair"
    assert len(result.assistant_message()["tool_calls"]) == 1
