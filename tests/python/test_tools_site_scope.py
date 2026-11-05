import asyncio

from app import tools
from app.errors import AgentError
from app.models import ToolCall


class FakeResponse:
    status_code = 200

    def json(self):
        return {"success": True, "data": {"site_id": "site-a"}}


class FakeAsyncClient:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json, headers):
        self.__class__.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse()


def context(site_ids, selected=None):
    return {
        "site_id": selected or (site_ids[0] if len(site_ids) == 1 else None),
        "_raw_token": "signed-context",
        "organization_id": "org-a",
        "correlation_id": "corr-1",
    }


def run_tool(tool_call, ctx, message=""):
    return asyncio.run(tools.execute_tool(tool_call, ctx, message))


def test_tool_uses_the_authorized_site_not_the_llm_supplied_id(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)

    run_tool(
        ToolCall(
            name="getSiteFairs",
            arguments={
                "site_id": "site-foreign",
                "organization_id": "org-foreign",
                "role": "SUPERADMIN",
            },
        ),
        context(["site-a", "site-b"], selected="site-a"),
    )

    payload = FakeAsyncClient.calls[0]["json"]
    assert payload["input"] == {"site_id": "site-a"}


def test_tool_rejects_multi_site_context(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)

    try:
        run_tool(
            ToolCall(name="getSiteFairs", arguments={}),
            context(["site-a", "site-b"]),
        )
    except AgentError as error:
        assert error.code == "AI_SITE_ASSIGNMENT_REQUIRED"
    else:
        raise AssertionError("tool executed without an authorized site selection")
    assert FakeAsyncClient.calls == []


def test_tool_works_without_any_site_id_from_the_model(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)

    run_tool(
        ToolCall(name="getSiteOverview", arguments={}),
        context(["site-a"], selected="site-a"),
    )

    assert FakeAsyncClient.calls[0]["json"]["input"] == {"site_id": "site-a"}
