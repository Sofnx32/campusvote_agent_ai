"""Deadline global absoluto: anclaje único, propagación y timeouts acotados."""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

import pytest

BASE = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def _deps():
    from app import agent, main, tools
    from app.config import settings
    from app.errors import AgentError
    from app.models import LLMResult, ToolCall
    return agent, main, tools, settings, AgentError, LLMResult, ToolCall


def iso(offset):
    return (BASE + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


class FakeClock:
    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ClockProvider:
    """Provider con la firma actual: complete(context, messages, allow_tools)."""

    def __init__(self, results, clock, consume=0.0):
        self.results, self.clock, self.consume = list(results), clock, consume
        self.calls = []

    async def complete(self, context, messages, allow_tools=True):
        self.calls.append({"allow_tools": allow_tools, "messages": list(messages)})
        self.clock.advance(self.consume)
        return self.results.pop(0)


class ToolSpy:
    def __init__(self, clock, consume=0.0):
        self.clock, self.consume, self.calls = clock, consume, []

    async def __call__(self, tool_call, context, message, remaining_seconds=None):
        self.calls.append((tool_call.name, remaining_seconds))
        self.clock.advance(self.consume)
        return {"success": True, "data": {"tool": tool_call.name}}


class WaitSpy:
    def __init__(self):
        self.timeouts = []

    async def __call__(self, awaitable, timeout):
        self.timeouts.append(timeout)
        return await awaitable


def patch_clocks(monkeypatch, main, mono_values, wall=None):
    seen = []

    def monotonic():
        seen.append(len(seen))
        return mono_values[min(len(seen) - 1, len(mono_values) - 1)]

    class FixedDateTime:
        fromisoformat = staticmethod(datetime.fromisoformat)
        fixed = wall or BASE

        @classmethod
        def now(cls, tz=None):
            return cls.fixed

    monkeypatch.setattr(main, "time", types.SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(main, "datetime", FixedDateTime)
    return seen


def context():
    return {"site_id": "site-a", "_raw_token": "signed", "correlation_id": "corr-1"}


# ---------------------------------------------------------------------------
# 1. Conversión monotónica absoluta sin deriva y sin ampliación
# ---------------------------------------------------------------------------

def test_absolute_deadline_single_clock_capture_and_config_cap(monkeypatch):
    _, main, _, settings, _, _, _ = _deps()
    seen = patch_clocks(monkeypatch, main, [500.0, 999.0])
    assert main.absolute_deadline(iso(25)) == 500.0 + settings.agent_deadline_seconds
    assert len(seen) == 1


def test_absolute_deadline_never_extends_received_deadline(monkeypatch):
    _, main, _, _, _, _, _ = _deps()
    patch_clocks(monkeypatch, main, [500.0])
    assert main.absolute_deadline(iso(3)) == 503.0
    assert main.absolute_deadline(iso(0.25)) == 500.25


def test_absolute_deadline_rejects_expired_and_invalid(monkeypatch):
    _, main, _, _, AgentError, _, _ = _deps()
    patch_clocks(monkeypatch, main, [500.0])
    for value in (iso(-1), "not-a-date", None):
        with pytest.raises(AgentError) as error:
            main.absolute_deadline(value)
        assert error.value.code == "AI_DEADLINE_EXCEEDED"
        assert error.value.status == 504


# ---------------------------------------------------------------------------
# 2-4. Deadline vencido, cercano y primera llamada LLM
# ---------------------------------------------------------------------------

def test_agent_aborts_before_llm_when_deadline_expired():
    agent, _, _, _, AgentError, LLMResult, ToolCall = _deps()
    clock = FakeClock()
    provider = ClockProvider([LLMResult(content="nunca")], clock)
    with pytest.raises(AgentError) as error:
        asyncio.run(agent.run_agent_turn(
            provider, {}, "m", [], ToolSpy(clock),
            max_tool_calls=5, deadline_at=999.0, clock=clock,
        ))
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert provider.calls == []


def test_near_expiry_consumed_by_llm_aborts_before_tool():
    agent, _, _, _, AgentError, LLMResult, ToolCall = _deps()
    clock = FakeClock()
    allowed = frozenset({"getSiteFairs"})
    provider = ClockProvider(
        [LLMResult(tool_calls=[ToolCall(id="1", name="getSiteFairs", arguments={})], allowed_tools=allowed)],
        clock, consume=0.5,
    )
    tool = ToolSpy(clock)
    with pytest.raises(AgentError) as error:
        asyncio.run(agent.run_agent_turn(
            provider, {}, "m", [], tool,
            max_tool_calls=5, deadline_at=1000.4, clock=clock,
        ))
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert tool.calls == []


def test_first_llm_receives_full_remaining_budget():
    agent, _, _, _, _, LLMResult, ToolCall = _deps()
    clock, wait = FakeClock(), WaitSpy()
    provider = ClockProvider([LLMResult(content="ok")], clock)
    asyncio.run(agent.run_agent_turn(
        provider, {}, "m", [], ToolSpy(clock),
        max_tool_calls=5, deadline_at=1020.0, clock=clock, wait_for=wait,
    ))
    assert wait.timeouts == [20.0]


# ---------------------------------------------------------------------------
# 5-8. Herramientas y segunda llamada LLM sin reinicio
# ---------------------------------------------------------------------------

def test_tools_and_second_llm_receive_monotonically_smaller_budgets():
    agent, _, _, _, _, LLMResult, ToolCall = _deps()
    clock, wait = FakeClock(), WaitSpy()
    allowed1 = frozenset({"getSiteFairs", "getSiteProjects"})
    allowed2 = frozenset()
    provider = ClockProvider([
        LLMResult(tool_calls=[
            ToolCall(id="1", name="getSiteFairs", arguments={}),
            ToolCall(id="2", name="getSiteProjects", arguments={}),
        ], allowed_tools=allowed1),
        LLMResult(content="ok", allowed_tools=allowed2),
    ], clock)
    tool = ToolSpy(clock, consume=2.0)
    asyncio.run(agent.run_agent_turn(
        provider, {}, "m", [], tool,
        max_tool_calls=5, deadline_at=1020.0, clock=clock, wait_for=wait,
    ))
    assert tool.calls == [("getSiteFairs", 20.0), ("getSiteProjects", 18.0)]
    # wait_for protege LLM1, herramienta a, herramienta b y LLM2; el ultimo
    # timeout es el restante real, sin reinicio del presupuesto.
    assert wait.timeouts == [20.0, 20.0, 18.0, 16.0]


def test_tool_started_with_less_than_one_second_receives_reduced_budget():
    agent, _, _, _, _, LLMResult, ToolCall = _deps()
    clock, wait = FakeClock(), WaitSpy()
    allowed1 = frozenset({"getSiteFairs"})
    allowed2 = frozenset()
    provider = ClockProvider([
        LLMResult(tool_calls=[ToolCall(id="1", name="getSiteFairs", arguments={})], allowed_tools=allowed1),
        LLMResult(content="ok", allowed_tools=allowed2),
    ], clock, consume=19.4)
    tool = ToolSpy(clock)
    asyncio.run(agent.run_agent_turn(
        provider, {}, "m", [], tool,
        max_tool_calls=5, deadline_at=1020.0, clock=clock, wait_for=wait,
    ))
    assert tool.calls == [("getSiteFairs", pytest.approx(0.6))]
    # LLM1=20.0, herramienta=0.6 y LLM2=0.6: la herramienta tambien queda
    # acotada por wait_for y el presupuesto no se reinicia.
    assert wait.timeouts[0] == 20.0
    assert wait.timeouts[1] == pytest.approx(0.6)
    assert wait.timeouts[2] == pytest.approx(0.6)


def test_agent_rechecks_deadline_after_each_tool():
    agent, _, _, _, AgentError, LLMResult, ToolCall = _deps()
    clock = FakeClock()
    allowed = frozenset({"getSiteFairs"})
    provider = ClockProvider([
        LLMResult(tool_calls=[ToolCall(id="1", name="getSiteFairs", arguments={})], allowed_tools=allowed),
    ], clock)
    tool = ToolSpy(clock, consume=20.0)
    with pytest.raises(AgentError) as error:
        asyncio.run(agent.run_agent_turn(
            provider, {}, "m", [], tool,
            max_tool_calls=5, deadline_at=1020.0, clock=clock,
        ))
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert len(tool.calls) == 1


# ---------------------------------------------------------------------------
# 11-12. Retrocompatibilidad con deadline_seconds y mocks existentes
# ---------------------------------------------------------------------------

def test_legacy_deadline_seconds_and_three_argument_tool_mocks():
    agent, _, _, _, _, LLMResult, ToolCall = _deps()
    called = []

    async def legacy_tool(tool_call, context, message):
        called.append(tool_call.name)
        return {"success": True}

    allowed1 = frozenset({"getSiteFairs"})
    allowed2 = frozenset()
    provider = ClockProvider([
        LLMResult(tool_calls=[ToolCall(id="1", name="getSiteFairs", arguments={})], allowed_tools=allowed1),
        LLMResult(content="ok", allowed_tools=allowed2),
    ], FakeClock())
    result, results = asyncio.run(agent.run_agent_turn(
        provider, {}, "m", [], legacy_tool,
        max_tool_calls=5, deadline_seconds=5,
    ))
    assert result.content == "ok"
    assert called == ["getSiteFairs"]
    assert len(results) == 1
    assert [call["allow_tools"] for call in provider.calls] == [True, True]
