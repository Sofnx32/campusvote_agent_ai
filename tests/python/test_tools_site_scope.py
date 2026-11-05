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


def test_fair_creation_is_sent_to_node_as_a_proposal_not_written_by_python(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)
    ctx = context(["site-a"], selected="site-a")
    ctx["conversation_id"] = "conversation-a"

    run_tool(
        ToolCall(name="createFair", arguments={
            "name": "Feria anual",
            "image_url": "https://files.example/cover.png",
        }),
        ctx,
        "Quiero preparar una feria",
    )

    request = FakeAsyncClient.calls[0]
    assert request["url"].endswith("/internal/ai/v1/tools/createFair")
    assert request["json"]["input"] == {
        "name": "Feria anual",
        "image_url": "https://files.example/cover.png",
        "site_id": "site-a",
    }
    assert request["json"]["conversation_id"] == "conversation-a"
    assert request["json"]["user_message"] == "Quiero preparar una feria"
    assert request["headers"]["X-AI-Context"] == "signed-context"


def test_teaching_criteria_tools_are_allowlisted_and_writes_stay_in_node(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)
    ctx = context(["site-a"], selected="site-a")
    run_tool(ToolCall(name="listTeachingCriteria", arguments={}), ctx, "Lista mis criterios")
    run_tool(ToolCall(name="createTeachingCriterion", arguments={"name": "Claridad docente"}), ctx, "Confirmo el criterio")
    assert FakeAsyncClient.calls[0]["url"].endswith("/listTeachingCriteria")
    assert FakeAsyncClient.calls[1]["url"].endswith("/createTeachingCriterion")
    assert FakeAsyncClient.calls[1]["json"]["input"] == {"name": "Claridad docente", "site_id": "site-a"}
