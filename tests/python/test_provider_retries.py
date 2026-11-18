"""Casos 5-8: reintentos acotados, estados permitidos y backoff presupuestado."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from provider_helpers import (
    MockResponse, content_response, make_provider, run, tool_response,
)

CONTEXT = {"site_id": "site-a"}
MESSAGES = [{"role": "user", "content": "ferias"}]
RETRIES = {"llm_max_retries": 2}


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transitional_5xx_are_retried(monkeypatch, status):
    captured = []
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=status, payload={"error": "private"}),
            content_response("ok después de reintento"),
        ],
        captured=captured, overrides=RETRIES,
    )
    result = run(Provider().complete(CONTEXT, MESSAGES))
    assert result.content == "ok después de reintento"
    assert len(captured) == 2


def test_429_with_retry_after_is_retried(monkeypatch):
    captured = []
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=429, payload={}, headers={"retry-after": "0.05"}),
            content_response("ok"),
        ],
        captured=captured, overrides=RETRIES,
    )
    assert run(Provider().complete(CONTEXT, MESSAGES)).content == "ok"
    assert len(captured) == 2


@pytest.mark.parametrize("status,expected_code", [
    (400, "LLM_INVALID_RESPONSE"),
    (401, "PROVIDER_AUTH_FAILED"),
    (403, "PROVIDER_AUTH_FAILED"),
    (404, "LLM_MODEL_NOT_FOUND"),
    (422, "LLM_INVALID_RESPONSE"),
])
def test_non_retryable_statuses_fail_on_first_attempt(monkeypatch, status, expected_code):
    captured = []
    Provider, AgentError = make_provider(
        monkeypatch,
        response=MockResponse(status=status, payload={"error": "private"}),
        captured=captured, overrides=RETRIES,
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == expected_code
    assert len(captured) == 1


def test_provider_auth_failed_never_maps_to_user_401(monkeypatch):
    Provider, AgentError = make_provider(
        monkeypatch, response=MockResponse(status=401, payload={}), overrides=RETRIES,
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "PROVIDER_AUTH_FAILED"
    assert error.value.status == 502
    assert error.value.details["origin_status"] == 401


def test_timeout_is_retried_and_classified(monkeypatch):
    captured = []
    Provider, AgentError = make_provider(
        monkeypatch, error=httpx.TimeoutException("timeout"),
        captured=captured, overrides=RETRIES,
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert len(captured) == 3


def test_connection_error_is_retried(monkeypatch):
    captured = []
    Provider, AgentError = make_provider(
        monkeypatch, error=httpx.ConnectError("connection refused"),
        captured=captured, overrides=RETRIES,
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert len(captured) == 3


def test_max_total_attempts_is_three(monkeypatch):
    captured = []
    Provider, _ = make_provider(
        monkeypatch,
        responses=[MockResponse(status=503, payload={}) for _ in range(3)],
        captured=captured,
        overrides={"llm_max_retries": 99},
    )
    with pytest.raises(Exception):
        run(Provider().complete(CONTEXT, MESSAGES))
    assert len(captured) == 3


def test_retry_after_is_respected(monkeypatch):
    slept: list[float] = []

    async def spy(delay):
        slept.append(delay)

    monkeypatch.setattr("app.providers.retry_policy.asyncio.sleep", spy)
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=429, payload={}, headers={"retry-after": "0.07"}),
            content_response("ok"),
        ],
        overrides=RETRIES,
    )
    run(Provider().complete(CONTEXT, MESSAGES))
    assert slept and slept[0] >= 0.07


def test_backoff_aborts_when_wait_exceeds_budget(monkeypatch):
    Provider, AgentError = make_provider(
        monkeypatch,
        response=MockResponse(status=429, payload={}, headers={"retry-after": "9"}),
        captured=[],
        overrides={
            **RETRIES,
            "llm_total_budget_seconds": 0.02,
            "llm_retry_min_next_attempt_seconds": 0.01,
        },
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert error.value.details["cause"] == "RATE_LIMITED"


def test_write_tools_are_not_reexecuted_on_provider_retry(monkeypatch):
    calls: list[str] = []

    async def fake_tool(tool_call, context, message):
        calls.append(tool_call.name)
        return {"success": True, "tool": tool_call.name, "data": {"status": "created"}}

    from app.agent import run_agent_turn
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=503, payload={}),
            tool_response("createFair", {"name": "Feria"}),
            content_response("Listo"),
        ],
        captured=[], overrides=RETRIES,
    )
    run(run_agent_turn(
        Provider(), CONTEXT, "crea la feria", [], fake_tool,
        max_tool_calls=2, deadline_seconds=5,
    ))
    assert calls == ["createFair"]
