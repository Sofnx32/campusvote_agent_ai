import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx
import pytest


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
    responses = None
    error = None
    request = None
    requests = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json, headers):
        self.__class__.request = {"url": url, "json": json, "headers": headers}
        self.__class__.requests.append(self.__class__.request)
        if self.error:
            raise self.error
        if self.__class__.responses:
            return self.__class__.responses.pop(0)
        return self.response


def run(coro):
    return asyncio.run(coro)


def tool_call_payload(calls):
    return {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        for call_id, name, arguments in calls
    ]}}]}


def setup_provider(monkeypatch, response=None, error=None, responses=None, **settings):
    """Set up test provider by patching environment variables and module cache."""
    # Clear module cache so modules re-import with patched settings
    for mod in list(sys.modules.keys()):
        if mod.startswith("app.providers.") or mod == "app.config" or mod == "app.llm":
            del sys.modules[mod]
    
    # Patch environment variables BEFORE importing
    defaults = {
        "LLM_PROVIDER": "openai-compatible",
        "LLM_URL": "https://llm.example/v1/chat/completions",
        "LLM_MODEL": "model-x",
        "LLM_API_KEY": "secret-test-key",
        "LLM_FALLBACK_URL": "",
        "LLM_FALLBACK_MODEL": "",
        "LLM_FALLBACK_API_KEY": "",
        "LLM_TIMEOUT_SECONDS": "2",
        "LLM_TOTAL_BUDGET_SECONDS": "5",
    }
    for key, value in {**defaults, **settings}.items():
        monkeypatch.setenv(key, value)
    
    # Also patch httpx client
    monkeypatch.setattr("app.providers.openai_compatible.httpx.AsyncClient", MockClient)
    MockClient.response = response
    MockClient.responses = list(responses) if responses else None
    MockClient.error = error
    MockClient.request = None
    MockClient.requests = []
    
    # Now import after patching
    from app.providers.openai_compatible import OpenAICompatibleProvider
    from app.errors import AgentError
    return OpenAICompatibleProvider, AgentError


FALLBACK_CONFIG = {
    "LLM_FALLBACK_URL": "https://fallback.example/v1/chat/completions",
    "LLM_FALLBACK_MODEL": "fallback-model",
    "LLM_FALLBACK_API_KEY": "fallback-key",
}


def test_openai_compatible_success_and_ferias_tool_intent(monkeypatch):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, MockResponse(payload=tool_call_payload([
        ("call-1", "getSiteFairs", {}),
    ])))

    result = run(OpenAICompatibleProvider().complete(
        {"site_id": "site-a", "site_name": "Sede Norte"},
        [{"role": "user", "content": "ferias"}],
    ))

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "getSiteFairs"
    assert result.tool_calls[0].arguments == {}
    request = MockClient.request
    assert request["url"].endswith("/v1/chat/completions")
    assert request["headers"]["Authorization"] == "Bearer secret-test-key"
    assert request["json"]["model"] == "model-x"
    assert request["json"]["messages"][0]["role"] == "system"
    assert request["json"]["messages"][-1] == {"role": "user", "content": "ferias"}
    assert request["json"]["tool_choice"] == "auto"
    exposed = {tool["function"]["name"] for tool in request["json"]["tools"]}
    assert "createFair" in exposed
    assert "createTeachingCriterion" in exposed


def test_provider_supports_multiple_tool_calls_in_one_turn(monkeypatch):
    OpenAICompatibleProvider, _ = setup_provider(monkeypatch, MockResponse(payload=tool_call_payload([
        ("call-1", "getSiteOverview", {}),
        ("call-2", "getSiteFairs", {}),
    ])))

    result = run(OpenAICompatibleProvider().complete(
        {"site_id": "site-a"}, [{"role": "user", "content": "resumen y ferias"}],
    ))

    assert [call.name for call in result.tool_calls] == ["getSiteOverview", "getSiteFairs"]
    assert [call.id for call in result.tool_calls] == ["call-1", "call-2"]
    assistant = result.assistant_message()
    assert assistant["role"] == "assistant"
    assert [call["id"] for call in assistant["tool_calls"]] == ["call-1", "call-2"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {}


def test_provider_forces_a_text_answer_when_tools_are_disabled(monkeypatch):
    OpenAICompatibleProvider, _ = setup_provider(monkeypatch, MockResponse(payload={"choices": [{"message": {"content": "Listo."}}]}))

    result = run(OpenAICompatibleProvider().complete(
        {"site_id": "site-a"}, [{"role": "user", "content": "hola"}], allow_tools=False,
    ))

    assert result.content == "Listo."
    assert result.tool_calls == []
    assert "tools" not in MockClient.request["json"]


def test_create_fair_schema_leaves_cover_to_the_authenticated_upload_card():
    from app.tool_schemas import tool_schema
    schema = tool_schema("createFair")
    assert schema["parameters"]["required"] == ["name"]
    assert "image_url" in schema["parameters"]["properties"]
    assert "No inventes image_url" in schema["description"]
    assert "site_id" not in schema["parameters"]["properties"]
    assert "organization_id" not in schema["parameters"]["properties"]
    assert "additionalProperties" not in schema["parameters"]


@pytest.mark.parametrize("status,code", [
    (401, "LLM_AUTH_FAILED"),
    (403, "LLM_AUTH_FAILED"),
    (404, "LLM_MODEL_NOT_FOUND"),
    (429, "LLM_RATE_LIMITED"),
    (500, "LLM_PROVIDER_UNAVAILABLE"),
])
def test_provider_http_errors_are_classified(monkeypatch, status, code):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, MockResponse(status=status, payload={"error": "private response"}))

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "ferias"}]))

    assert error.value.code == code
    if status == 429:
        assert error.value.status == 429


def test_provider_timeout_is_classified(monkeypatch):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, error=httpx.ReadTimeout("timeout"))

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "ferias"}]))

    assert error.value.code == "LLM_PROVIDER_TIMEOUT"


@pytest.mark.parametrize("payload", [ValueError("invalid JSON"), {}, {"choices": []}, {"choices": [{"message": None}]}])
def test_invalid_provider_responses_are_classified(monkeypatch, payload):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, MockResponse(payload=payload))

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "ferias"}]))

    assert error.value.code == "LLM_INVALID_RESPONSE"


def test_unknown_tool_names_are_returned_to_the_model(monkeypatch):
    OpenAICompatibleProvider, _ = setup_provider(monkeypatch, MockResponse(payload=tool_call_payload([
        ("call-1", "dropDatabase", {}),
    ])))

    result = run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "x"}]))

    assert result.tool_calls[0].name == "dropDatabase"
    assert result.tool_calls[0].id == "call-1"


def test_missing_configuration_has_specific_error(monkeypatch):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, LLM_API_KEY="")

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "ferias"}]))

    assert error.value.code == "LLM_CONFIG_MISSING"


def test_rate_limited_primary_engages_the_fallback_provider(monkeypatch):
    OpenAICompatibleProvider, _ = setup_provider(
        monkeypatch,
        responses=[
            MockResponse(status=429, payload={"error": "quota"}),
            MockResponse(payload={"choices": [{"message": {"content": "Respuesta del respaldo."}}]}),
        ],
        **FALLBACK_CONFIG,
    )

    result = run(OpenAICompatibleProvider().complete(
        {"site_id": "site-a"}, [{"role": "user", "content": "hola"}],
    ))

    assert result.content == "Respuesta del respaldo."
    assert len(MockClient.requests) == 2
    primary, secondary = MockClient.requests
    assert primary["json"]["model"] == "model-x"
    assert secondary["url"] == "https://fallback.example/v1/chat/completions"
    assert secondary["headers"]["Authorization"] == "Bearer fallback-key"
    assert secondary["json"]["model"] == "fallback-model"
    assert secondary["json"]["messages"][0]["role"] == "system"
    assert secondary["json"]["tools"]


def test_fallback_keeps_the_openai_tool_protocol_messages(monkeypatch):
    OpenAICompatibleProvider, _ = setup_provider(
        monkeypatch,
        responses=[
            MockResponse(status=503, payload={"error": "down"}),
            MockResponse(payload=tool_call_payload([("call-9", "getSiteFairs", {})])),
        ],
        **FALLBACK_CONFIG,
    )

    result = run(OpenAICompatibleProvider().complete(
        {"site_id": "site-a"}, [{"role": "user", "content": "ferias"}],
    ))

    assert [call.name for call in result.tool_calls] == ["getSiteFairs"]
    assert MockClient.requests[1]["json"]["tool_choice"] == "auto"


def test_primary_error_is_propagated_without_fallback_configuration(monkeypatch):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, MockResponse(status=429, payload={"error": "quota"}))

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "hola"}]))

    assert error.value.code == "LLM_RATE_LIMITED"
    assert len(MockClient.requests) == 1


def test_invalid_primary_response_does_not_engage_the_fallback(monkeypatch):
    OpenAICompatibleProvider, AgentError = setup_provider(monkeypatch, MockResponse(payload={"choices": []}), **FALLBACK_CONFIG)

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "hola"}]))

    assert error.value.code == "LLM_INVALID_RESPONSE"
    assert len(MockClient.requests) == 1


def test_fallback_requires_url_model_and_key_together(monkeypatch):
    OpenAICompatibleProvider, AgentError = setup_provider(
        monkeypatch,
        MockResponse(status=429, payload={"error": "quota"}),
        LLM_FALLBACK_URL="https://fallback.example/v1/chat/completions",
        LLM_FALLBACK_MODEL="fallback-model",
        LLM_FALLBACK_API_KEY="",
    )

    with pytest.raises(AgentError) as error:
        run(OpenAICompatibleProvider().complete({"site_id": "site-a"}, [{"role": "user", "content": "hola"}]))

    assert error.value.code == "LLM_RATE_LIMITED"
    assert len(MockClient.requests) == 1