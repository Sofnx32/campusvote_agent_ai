"""Casos 9-10: fallback con presupuesto reservado y logs explícitos."""

from __future__ import annotations

import pytest

from provider_helpers import (
    LogCapture, MockResponse, content_response, make_provider, run, tool_response,
)

CONTEXT = {"site_id": "site-a"}
MESSAGES = [{"role": "user", "content": "hola"}]
FALLBACK = {
    "llm_fallback_url": "https://fallback.example/v1/chat/completions",
    "llm_fallback_model": "fallback-model",
    "llm_fallback_api_key": "fallback-key",
}


def test_rate_limited_primary_engages_fallback(monkeypatch):
    captured = []
    with LogCapture("app.providers.telemetry") as logs:
        Provider, _ = make_provider(
            monkeypatch,
            responses=[
                MockResponse(status=429, payload={}, headers={"retry-after": "0"}),
                content_response("Respuesta del respaldo."),
            ],
            captured=captured, overrides=FALLBACK,
        )
        result = run(Provider().complete(CONTEXT, MESSAGES))
    assert result.content == "Respuesta del respaldo."
    assert captured[1]["url"] == FALLBACK["llm_fallback_url"]
    assert captured[1]["json"]["model"] == "fallback-model"
    assert len(logs.events("llm_fallback_engaged")) == 1


def test_fallback_keeps_tool_protocol(monkeypatch):
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=503, payload={}),
            tool_response("getSiteFairs", {}),
        ],
        overrides=FALLBACK,
    )
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert result.tool_calls[0].name == "getSiteFairs"
    assert result.tool_calls[0].arguments == {}


def test_missing_fallback_is_logged_explicitly(monkeypatch):
    with LogCapture("app.providers.telemetry") as logs:
        Provider, AgentError = make_provider(
            monkeypatch, response=MockResponse(status=429, payload={}),
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "RATE_LIMITED"
    events = logs.events("llm_fallback_not_configured")
    assert len(events) == 1
    assert events[0]["cause"] == "RATE_LIMITED"


def test_invalid_primary_response_does_not_engage_fallback(monkeypatch):
    with LogCapture("app.providers.telemetry") as logs:
        Provider, AgentError = make_provider(
            monkeypatch,
            response=MockResponse(payload={"choices": []}),
            overrides=FALLBACK,
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "LLM_INVALID_RESPONSE"
    assert logs.events("llm_fallback_engaged") == []


def test_fallback_skipped_when_budget_is_insufficient(monkeypatch):
    with LogCapture("app.providers.telemetry") as logs:
        Provider, AgentError = make_provider(
            monkeypatch,
            response=MockResponse(status=429, payload={}),
            overrides={
                **FALLBACK,
                "llm_total_budget_seconds": 1.0,
                "llm_fallback_budget_seconds": 0.9,
                "llm_retry_min_next_attempt_seconds": 0.5,
            },
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "RATE_LIMITED"
    assert len(logs.events("llm_fallback_skipped_insufficient_budget")) == 1


def test_partial_fallback_configuration_is_ignored(monkeypatch):
    with LogCapture("app.providers.telemetry") as logs:
        Provider, AgentError = make_provider(
            monkeypatch,
            response=MockResponse(status=429, payload={}),
            overrides={"llm_fallback_url": FALLBACK["llm_fallback_url"]},
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "RATE_LIMITED"
    assert len(logs.events("llm_fallback_not_configured")) == 1


def test_auth_failure_does_not_engage_fallback(monkeypatch):
    with LogCapture("app.providers.telemetry") as logs:
        Provider, AgentError = make_provider(
            monkeypatch,
            response=MockResponse(status=401, payload={}),
            overrides=FALLBACK,
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "PROVIDER_AUTH_FAILED"
    assert logs.events("llm_fallback_engaged") == []


def test_fallback_preserves_multiple_tool_calls(monkeypatch):
    """Regresión 3.2: el fallback conserva todas las tool_calls sin filtrarlas."""
    payload = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "f1", "function": {"name": "getSiteFairs", "arguments": "{}"}},
        {"id": "f2", "function": {"name": "listFairStands", "arguments": "{}"}},
    ]}}]}
    Provider, _ = make_provider(
        monkeypatch,
        responses=[MockResponse(status=503, payload={}), MockResponse(payload=payload)],
        overrides=FALLBACK,
    )
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert [(call.id, call.name) for call in result.tool_calls] == [
        ("f1", "getSiteFairs"), ("f2", "listFairStands"),
    ]
