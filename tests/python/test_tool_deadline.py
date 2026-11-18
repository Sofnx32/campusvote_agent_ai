"""Timeout de herramienta acotado y cancelación de LLM por deadline global."""

from __future__ import annotations

import asyncio

import pytest


def _deps():
    from app import agent, tools
    from app.config import settings
    from app.errors import AgentError
    from app.models import LLMResult, ToolCall
    return agent, tools, settings, AgentError, LLMResult, ToolCall


class FakeClock:
    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value


class ClockProvider:
    async def complete(self, context, messages, allow_tools=True):
        return self.result

    def __init__(self, result):
        self.result = result


class ToolSpy:
    def __init__(self):
        self.calls = []

    async def __call__(self, tool_call, context, message, remaining_seconds=None):
        self.calls.append((tool_call.name, remaining_seconds))
        return {"success": True, "data": {}}


def context():
    return {"site_id": "site-a", "_raw_token": "signed", "correlation_id": "corr-1"}


def fake_client(record):
    """AsyncClient determinista: registra el timeout efectivo de cada llamada."""
    class Client:
        def __init__(self, **kwargs):
            record.append(kwargs["timeout"])
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, json, headers):
            return type("R", (), {"status_code": 200,
                                  "json": lambda self: {"success": True}})()

    return Client


def test_tool_timeout_never_exceeds_global_remaining(monkeypatch):
    _, tools, settings, AgentError, _, ToolCall = _deps()
    created = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", fake_client(created))
    call = ToolCall(id="1", name="getSiteFairs", arguments={})
    asyncio.run(tools.execute_tool(call, context(), "", remaining_seconds=0.6))
    assert created[-1] == 0.6
    asyncio.run(tools.execute_tool(call, context(), ""))
    assert created[-1] == settings.tool_timeout_seconds
    with pytest.raises(AgentError) as error:
        asyncio.run(tools.execute_tool(call, context(), "", remaining_seconds=0))
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert len(created) == 2


def test_tool_timeout_uses_strict_min_without_floor(monkeypatch):
    _, tools, _, _, _, ToolCall = _deps()
    created = []
    monkeypatch.setattr(tools.httpx, "AsyncClient", fake_client(created))
    call = ToolCall(id="1", name="getSiteFairs", arguments={})
    asyncio.run(tools.execute_tool(call, context(), "", remaining_seconds=0.05))
    assert created == [0.05]


def test_llm_timeout_is_translated_to_deadline_error():
    agent, _, _, AgentError, LLMResult, _ = _deps()
    provider = ClockProvider(LLMResult(content="nunca"))

    async def cancel(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    with pytest.raises(AgentError) as error:
        asyncio.run(agent.run_agent_turn(
            provider, {}, "m", [], ToolSpy(),
            max_tool_calls=5, deadline_at=1020.0, clock=FakeClock(),
            wait_for=cancel,
        ))
    assert (error.value.code, error.value.status, error.value.retryable) == ("AI_DEADLINE_EXCEEDED", 504, True)


class WaitForStub:
    """Simula `wait_for` sin esperas reales; expira en `timeout_on` (1-based)."""

    def __init__(self, timeout_on=None):
        self.timeout_on, self.calls = timeout_on, []

    async def __call__(self, awaitable, timeout=None):
        index = len(self.calls) + 1
        self.calls.append(timeout)
        if index != self.timeout_on:
            return await awaitable
        task = asyncio.ensure_future(awaitable)
        await asyncio.sleep(0)  # solo cede el control; permite que arranque
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise asyncio.TimeoutError


class SequenceProvider:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def complete(self, context, messages, allow_tools=True):
        self.calls.append(messages)
        return self.results.pop(0)


def _tool_call():
    from app.models import ToolCall
    return ToolCall(id="1", name="a", arguments={})


# --- A. Herramienta excede el deadline: cancelada, sin segunda llamada LLM ---
def test_tool_exceeding_deadline_is_cancelled_and_aborts_turn():
    agent, _, _, AgentError, LLMResult, _ = _deps()
    state = []

    async def hanging_tool(tool_call, context, message, remaining_seconds=None):
        state.append("started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    provider = SequenceProvider([
        LLMResult(tool_calls=[_tool_call()]),
        LLMResult(content="nunca"),
    ])
    wait = WaitForStub(timeout_on=2)  # 1 = LLM, 2 = herramienta
    with pytest.raises(AgentError) as error:
        asyncio.run(agent.run_agent_turn(
            provider, {}, "m", [], hanging_tool,
            max_tool_calls=5, deadline_at=1020.0, clock=FakeClock(), wait_for=wait,
        ))
    assert (error.value.code, error.value.status, error.value.retryable) == ("AI_DEADLINE_EXCEEDED", 504, True)
    assert state == ["started", "cancelled"]
    assert len(provider.calls) == 1  # no hubo segunda llamada LLM
    assert wait.calls == [20.0, 20.0]


# --- B. Ejecutor antiguo de 3 argumentos queda igualmente protegido ---
def test_legacy_three_argument_tool_is_also_protected():
    agent, _, _, AgentError, LLMResult, _ = _deps()
    state = []

    async def legacy_tool(tool_call, context, message):
        state.append("started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    provider = ClockProvider(LLMResult(tool_calls=[_tool_call()]))
    wait = WaitForStub(timeout_on=2)
    with pytest.raises(AgentError) as error:
        asyncio.run(agent.run_agent_turn(
            provider, {}, "m", [], legacy_tool,
            max_tool_calls=5, deadline_at=1020.0, clock=FakeClock(), wait_for=wait,
        ))
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert state == ["started", "cancelled"]  # cancelado sin TypeError de firma
    assert wait.calls == [20.0, 20.0]


# --- C. Herramienta a tiempo: conserva su resultado y el ciclo continúa ---
def test_tool_finishing_in_time_preserves_result_and_continues():
    agent, _, _, _, LLMResult, _ = _deps()
    seen = []

    async def fast_tool(tool_call, context, message, remaining_seconds=None):
        seen.append(remaining_seconds)
        return {"success": True, "data": {"tool": tool_call.name}}

    provider = SequenceProvider([
        LLMResult(tool_calls=[_tool_call()]),
        LLMResult(content="listo"),
    ])
    wait = WaitForStub()
    result, results = asyncio.run(agent.run_agent_turn(
        provider, {}, "m", [], fast_tool,
        max_tool_calls=5, deadline_at=1020.0, clock=FakeClock(), wait_for=wait,
    ))
    assert result.content == "listo"
    assert results == [{"success": True, "data": {"tool": "a"}}]
    assert seen == [20.0]
    assert wait.calls == [20.0, 20.0, 20.0]  # LLM1, herramienta, LLM2
    assert len(provider.calls) == 2


# --- D. Timeout => deadline; AgentError funcional conserva su código ---
def test_timeout_vs_functional_agent_error():
    agent, _, _, AgentError, LLMResult, _ = _deps()

    # Ramas correctable y no correctable: ambas conservan su código semántico.
    for code, status, expect_raised in (
        ("AI_CONFIRMATION_REQUIRED", 409, False),
        ("AI_SCOPE_FORBIDDEN", 403, True),
    ):
        async def failing_tool(tool_call, ctx, message, remaining_seconds=None):
            raise AgentError(code, "fallo funcional", status, False)

        provider = SequenceProvider([
            LLMResult(tool_calls=[_tool_call()]),
            LLMResult(content="listo"),
        ])
        if expect_raised:
            with pytest.raises(AgentError) as error:
                asyncio.run(agent.run_agent_turn(
                    provider, {}, "m", [], failing_tool, max_tool_calls=5,
                    deadline_at=1020.0, clock=FakeClock(), wait_for=WaitForStub(),
                ))
            assert error.value.code == code and error.value.status == status
        else:
            _, results = asyncio.run(agent.run_agent_turn(
                provider, {}, "m", [], failing_tool, max_tool_calls=5,
                deadline_at=1020.0, clock=FakeClock(), wait_for=WaitForStub(),
            ))
            assert results[0]["error"]["code"] == code
            assert "AI_DEADLINE_EXCEEDED" not in str(results)

