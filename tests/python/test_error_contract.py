import asyncio
import httpx
from starlette.requests import Request

from app.errors import AgentError, ERROR_CATALOG, error_payload
from app.main import agent_error_handler, unexpected_error_handler, app as fastapi_app


def request_with_id(request_id="request-python-123"):
    scope = {"type": "http", "method": "POST", "path": "/v1/chat", "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http", "client": ("test", 1), "http_version": "1.1", "root_path": ""}
    request = Request(scope)
    request.state.request_id = request_id
    return request


def test_error_payload_has_stable_shape_and_reference_id():
    body = error_payload(AgentError("LLM_PROVIDER_UNAVAILABLE", "El asistente no está disponible.", 503, True), "req-42")
    assert body == {
        "success": False,
        "error": {"code": "LLM_PROVIDER_UNAVAILABLE", "message": "El asistente está temporalmente fuera de servicio.", "retryable": True, "requestId": "req-42", "details": None},
        "requestId": "req-42",
        "meta": {"correlation_id": "req-42"},
    }


def test_catalog_covers_requested_domains():
    assert {"AUTH", "AUTHORIZATION", "VALIDATION", "NETWORK", "AI_PROVIDER", "AI_SERVICE", "DATABASE", "REDIS", "BUSINESS_OPERATION", "INTERNAL"} <= ERROR_CATALOG.keys()


def test_fastapi_handlers_return_json_safe_errors_with_request_id():
    request = request_with_id()
    known = asyncio.run(agent_error_handler(request, AgentError("LLM_PROVIDER_UNAVAILABLE", "provider failed", 503)))
    unexpected = asyncio.run(unexpected_error_handler(request, RuntimeError("password=secret")))
    assert known.status_code == 503
    assert known.body.find(b"request-python-123") >= 0
    assert unexpected.status_code == 500
    assert b"password" not in unexpected.body
    assert b"AI_INTERNAL_ERROR" in unexpected.body


def test_fastapi_validation_error_uses_the_same_json_contract():
    async def run_request():
        transport = httpx.ASGITransport(app=fastapi_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/chat",
                headers={"X-Correlation-Id": "req-pydantic-invalid"},
                json={"message": ""},
            )

    response = asyncio.run(run_request())
    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert body["error"]["code"] == "VALIDATION_INVALID_REQUEST"
    assert body["requestId"] == "req-pydantic-invalid"
    assert body["error"]["details"] is None
