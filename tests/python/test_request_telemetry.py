"""Casos 11-13: telemetría correlacionada, secretos y cadena de deadlines."""

from __future__ import annotations

import asyncio
import json

import pytest

from provider_helpers import (
    LogCapture, MockResponse, content_response, make_provider, run,
)
from app.config import deadline_chain, validate_deadline_chain
from app.errors import AgentError, error_payload
from app.observability import RequestMetrics, metrics_context

CONTEXT = {"site_id": "site-a"}
MESSAGES = [{"role": "user", "content": "hola"}]


async def _with_metrics(coro, metrics):
    token = metrics_context.set(metrics)
    try:
        return await coro
    finally:
        metrics_context.reset(token)


def run_with_metrics(coro, metrics):
    return asyncio.run(_with_metrics(coro, metrics))


def test_metrics_payload_shape():
    metrics = RequestMetrics(request_id="req-42", llm_calls=2, retries=1)
    metrics.record_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    payload = metrics.to_payload()
    assert payload["event"] == "request_metrics"
    assert payload["requestId"] == payload["correlation_id"] == "req-42"
    assert payload["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert set(payload["latency_ms"]) == {"total", "provider", "tools"}
    assert payload["counts"]["llm_calls"] == 2
    assert payload["counts"]["retries"] == 1
    assert "started" not in payload


def test_provider_updates_correlated_metrics(monkeypatch):
    metrics = RequestMetrics(request_id="req-metrics")
    captured = []
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=429, payload={}, headers={"retry-after": "0"}),
            content_response("ok", usage={"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}),
        ],
        captured=captured,
        overrides={"llm_max_retries": 2},
    )
    run_with_metrics(Provider().complete(CONTEXT, MESSAGES), metrics)
    assert metrics.llm_calls == 2
    assert metrics.retries == 1
    assert metrics.rate_limited_429 == 1
    assert metrics.provider_latency_ms >= 0
    assert metrics.total_tokens == 12
    assert metrics.prompt_tokens == 8 and metrics.completion_tokens == 4


def test_fallback_and_timeout_counters(monkeypatch):
    metrics = RequestMetrics(request_id="req-fallback")
    Provider, _ = make_provider(
        monkeypatch,
        responses=[
            MockResponse(status=503, payload={}),
            content_response("ok respaldo"),
        ],
        overrides={
            "llm_fallback_url": "https://fallback.example/v1/chat/completions",
            "llm_fallback_model": "fallback-model",
            "llm_fallback_api_key": "fallback-key",
        },
    )
    run_with_metrics(Provider().complete(CONTEXT, MESSAGES), metrics)
    assert metrics.fallbacks == 1
    assert metrics.llm_calls == 2


def test_no_secrets_or_pii_in_provider_logs(monkeypatch):
    secrets = ("super-secret-key-123", "Bearer", "cv_refresh_token", "juan@example.com")
    loggers = [
        "app.providers.openai_compatible", "app.providers.http_transport",
        "app.providers.response_parser", "app.providers.telemetry",
        "app.observability", "app.agent",
    ]
    captures = [LogCapture(name) for name in loggers]
    try:
        for capture in captures:
            capture.__enter__()
        Provider, _ = make_provider(
            monkeypatch,
            response=content_response("respuesta"),
            captured=[],
            overrides={"llm_api_key": "super-secret-key-123"},
        )
        run(Provider().complete(CONTEXT, [{"role": "user", "content": "juan@example.com"}]))
        combined = "\n".join(capture.text() for capture in captures)
        for secret in secrets:
            assert secret not in combined
        for capture in captures:
            for record in capture.records:
                payload = json.loads(record)
                assert set(payload).isdisjoint({"headers", "messages", "api_key", "apiKey", "token"})
    finally:
        for capture in captures:
            capture.__exit__(None, None, None)


def test_error_payload_never_includes_raw_provider_body(monkeypatch):
    error = AgentError(
        "PROVIDER_AUTH_FAILED", "Bearer super-secret-key-123", 502, False,
        details={"origin_status": 401},
    )
    payload = error_payload(error, "req-1")
    assert "super-secret-key-123" not in json.dumps(payload)
    assert payload["error"]["code"] == "PROVIDER_AUTH_FAILED"
    assert payload["error"]["retryable"] is False


def test_deadline_chain_default_is_coherent():
    chain = deadline_chain()
    assert chain["ai_context_jwt_ttl"] == 90.0
    assert chain["python_agent_deadline"] < chain["node_http_timeout"] < chain["ai_context_jwt_ttl"]
    assert 0 < chain["llm_fallback_budget"] < chain["llm_total_budget"]
    assert validate_deadline_chain() == []


def test_deadline_chain_reports_misconfiguration(monkeypatch):
    monkeypatch.setenv("AI_CHAT_TIMEOUT_MS", "10000")
    monkeypatch.setenv("AI_CONTEXT_TTL_SECONDS", "5")
    warnings = validate_deadline_chain()
    assert "python_agent_deadline must be lower than node_http_timeout" in warnings
    assert "node_http_timeout must be lower than ai_context_jwt_ttl" in warnings
