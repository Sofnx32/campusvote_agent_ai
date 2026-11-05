import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app import llm
from app.errors import AgentError


class MockResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class MockClient:
    response = None
    error = None
    request = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json, headers):
        self.__class__.request = {"url": url, "json": json, "headers": headers}
        if self.error:
            raise self.error
        return self.response


def setup_provider(monkeypatch, response=None, error=None, **settings):
    monkeypatch.setattr(llm.httpx, "AsyncClient", MockClient)
    MockClient.response = response
    MockClient.error = error
    MockClient.request = None
    values = {
        "llm_provider": "openai-compatible",
        "llm_url": "https://llm.example/v1/chat/completions",
        "llm_model": "model-x",
        "llm_api_key": "secret-test-key",
        "llm_timeout_seconds": 2,
        **settings,
    }
    monkeypatch.setattr(llm, "settings", SimpleNamespace(**values))


def run(coro):
    return asyncio.run(coro)


def test_openai_compatible_success_and_ferias_tool_intent(monkeypatch):
    setup_provider(monkeypatch, MockResponse(payload={"choices": [{"message": {
        "tool_calls": [{"function": {"name": "getSiteFairs", "arguments": "{}"}}]
    }}]}))

    result = run(llm.OpenAICompatibleProvider().complete(
        "ferias", {"site_id": "site-a", "site_name": "Sede Norte"}, []
    ))

    assert result.tool_call.name == "getSiteFairs"
    assert result.tool_call.arguments == {}
    assert MockClient.request["url"].endswith("/v1/chat/completions")
    assert MockClient.request["headers"]["Authorization"] == "Bearer secret-test-key"
    assert MockClient.request["json"]["model"] == "model-x"


@pytest.mark.parametrize("status,code", [
    (401, "LLM_AUTH_FAILED"),
    (403, "LLM_AUTH_FAILED"),
    (404, "LLM_MODEL_NOT_FOUND"),
    (429, "LLM_RATE_LIMITED"),
    (500, "LLM_PROVIDER_UNAVAILABLE"),
])
def test_provider_http_errors_are_classified(monkeypatch, status, code):
    setup_provider(monkeypatch, MockResponse(status=status, payload={"error": "private response"}))

    with pytest.raises(AgentError) as error:
        run(llm.OpenAICompatibleProvider().complete("ferias", {}, []))

    assert error.value.code == code


def test_provider_timeout_is_classified(monkeypatch):
    setup_provider(monkeypatch, error=httpx.ReadTimeout("timeout"))

    with pytest.raises(AgentError) as error:
        run(llm.OpenAICompatibleProvider().complete("ferias", {}, []))

    assert error.value.code == "LLM_PROVIDER_TIMEOUT"


@pytest.mark.parametrize("payload", [ValueError("invalid JSON"), {}, {"choices": []}, {"choices": [{"message": None}]}])
def test_invalid_provider_responses_are_classified(monkeypatch, payload):
    setup_provider(monkeypatch, MockResponse(payload=payload))

    with pytest.raises(AgentError) as error:
        run(llm.OpenAICompatibleProvider().complete("ferias", {}, []))

    assert error.value.code == "LLM_INVALID_RESPONSE"


def test_missing_configuration_has_specific_error(monkeypatch):
    setup_provider(monkeypatch, llm_api_key="")

    with pytest.raises(AgentError) as error:
        run(llm.OpenAICompatibleProvider().complete("ferias", {}, []))

    assert error.value.code == "LLM_CONFIG_MISSING"
