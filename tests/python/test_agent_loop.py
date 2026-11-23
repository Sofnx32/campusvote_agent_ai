import asyncio
import json

import pytest

from app.agent import run_agent_turn
from app.errors import AgentError
from app.models import LLMResult, ToolCall


class ScriptedProvider:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def complete(self, context, messages, allow_tools=True):
        self.calls.append({
            "allow_tools": allow_tools,
            "messages": [dict(message) for message in messages],
        })
        return self.results.pop(0)


def run(coro):
    return asyncio.run(coro)


def test_loop_sends_assistant_tool_calls_and_tool_results_in_order():
    allowed = frozenset({"getSiteOverview", "getSiteFairs"})
    provider = ScriptedProvider([
        LLMResult(tool_calls=[
            ToolCall(id="call-1", name="getSiteOverview", arguments={}),
            ToolCall(id="call-2", name="getSiteFairs", arguments={}),
        ], allowed_tools=allowed),
        LLMResult(content="Tienes 2 ferias.", allowed_tools=frozenset()),
    ])
    executed = []

    async def execute(tool_call, context, message):
        executed.append(tool_call.name)
        return {"success": True, "data": {"tool": tool_call.name}}

    result, results = run(run_agent_turn(
        provider,
        {"site_id": "site-a"},
        "resumen",
        [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "Hola."}],
        execute,
        max_tool_calls=5,
    ))

    assert result.content == "Tienes 2 ferias."
    assert executed == ["getSiteOverview", "getSiteFairs"]
    assert len(results) == 2
    second = provider.calls[1]["messages"]
    assert second[0] == {"role": "user", "content": "hola"}
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "call-2"
    assistant = next(message for message in second if message["role"] == "assistant" and message.get("tool_calls"))
    assert [call["id"] for call in assistant["tool_calls"]] == ["call-1", "call-2"]


def test_loop_forces_a_final_text_answer_after_the_tool_budget():
    allowed = frozenset({"getSiteFairs"})
    provider = ScriptedProvider([
        LLMResult(tool_calls=[ToolCall(id="call-1", name="getSiteFairs", arguments={})], allowed_tools=allowed),
        LLMResult(content="Resumen final.", allowed_tools=frozenset()),
    ])
    executed = []

    async def execute(tool_call, context, message):
        executed.append(tool_call.name)
        return {"success": True, "data": {}}

    result, _ = run(run_agent_turn(
        provider,
        {"site_id": "site-a"},
        "consulta",
        [],
        execute,
        max_tool_calls=1,
    ))

    assert result.content == "Resumen final."
    assert executed == ["getSiteFairs"]
    assert [call["allow_tools"] for call in provider.calls] == [True, False]


def test_loop_stops_when_the_deadline_is_exceeded():
    allowed = frozenset({"getSiteFairs"})
    provider = ScriptedProvider([
        LLMResult(tool_calls=[ToolCall(id="call-1", name="getSiteFairs", arguments={})], allowed_tools=allowed),
    ])
    clock_values = iter([0.0, 30.0])

    async def execute(tool_call, context, message):
        return {"success": True, "data": {}}

    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            provider,
            {"site_id": "site-a"},
            "consulta",
            [],
            execute,
            max_tool_calls=5,
            clock=lambda: next(clock_values),
        ))

    assert error.value.code == "AI_DEADLINE_EXCEEDED"


def test_loop_returns_correctable_tool_errors_to_the_model():
    allowed = frozenset({"createFair"})
    provider = ScriptedProvider([
        LLMResult(tool_calls=[ToolCall(id="call-1", name="createFair", arguments={"starts_at": "2026-11-20"})], allowed_tools=allowed),
        LLMResult(content="¿A qué hora inicia y termina la feria?", allowed_tools=frozenset()),
    ])

    async def execute(tool_call, context, message):
        raise AgentError(
            "AI_INVALID_REQUEST",
            "Indica la hora de inicio",
            400,
            public_message="Indica la hora de inicio de la feria.",
            details={"field": "starts_at"},
        )

    result, results = run(run_agent_turn(
        provider,
        {"site_id": "site-a"},
        "crea una feria el 20 de noviembre",
        [],
        execute,
        max_tool_calls=5,
    ))

    assert result.content.startswith("¿A qué hora")
    assert results[0]["success"] is False
    tool_message = provider.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    payload = json.loads(tool_message["content"])
    assert payload["error"] == {
        "code": "AI_INVALID_REQUEST",
        "message": "Indica la hora de inicio de la feria.",
        "details": {"field": "starts_at"},
    }


def test_loop_still_aborts_on_non_correctable_tool_errors():
    allowed = frozenset({"createFair"})
    provider = ScriptedProvider([
        LLMResult(tool_calls=[ToolCall(id="call-1", name="createFair", arguments={})], allowed_tools=allowed),
    ])

    async def execute(tool_call, context, message):
        raise AgentError("AI_SCOPE_FORBIDDEN", "Sede no autorizada", 403)

    with pytest.raises(AgentError) as error:
        run(run_agent_turn(provider, {"site_id": "site-a"}, "crea", [], execute, max_tool_calls=5))

    assert error.value.code == "AI_SCOPE_FORBIDDEN"
