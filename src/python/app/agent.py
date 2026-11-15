from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

from .errors import AgentError
from .models import LLMResult


async def run_agent_turn(
    provider,
    context: dict,
    message: str,
    history: list[dict],
    execute_tool: Callable[..., Awaitable[dict]],
    *,
    max_tool_calls: int,
    deadline_seconds: float = 25.0,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[LLMResult, list[dict]]:
    """Run the tool-calling loop using the standard OpenAI message protocol.

    The model may request several tools in a single turn. Every request gets an
    assistant message with its ``tool_calls`` followed by one ``tool`` message
    per call, which is what OpenAI-compatible providers (including Gemini's
    compatibility endpoint) require before the next completion.
    """
    results: list[dict] = []
    messages: list[dict] = [*history, {"role": "user", "content": message}]
    executed = 0
    started = clock()
    while True:
        allow_tools = executed < max_tool_calls
        current = await provider.complete(context, messages, allow_tools=allow_tools)
        if not current.tool_calls:
            break
        remaining = max_tool_calls - executed
        if remaining <= 0:
            break
        calls = current.tool_calls[:remaining]
        current.tool_calls = calls
        # Some providers omit the tool-call id. Assign one before building the
        # assistant message so the paired `tool` message references the same id.
        for offset, call in enumerate(calls, start=1):
            if not call.id:
                call.id = f"call_{executed + offset}"
        messages.append(current.assistant_message())
        for call in calls:
            result = await execute_tool(call, context, message)
            executed += 1
            results.append(result)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False),
            })
            if clock() - started > deadline_seconds:
                raise AgentError("AI_DEADLINE_EXCEEDED", "Global deadline exceeded", 504, True)
    return current, results
