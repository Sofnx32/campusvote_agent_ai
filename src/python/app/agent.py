from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable

from .errors import AgentError, CORRECTABLE_CODES
from .models import LLMResult
from .observability import current_metrics, request_id_context
from .providers.tool_registry import TOOLS
from .providers.tool_selector import select_tool_names

logger = logging.getLogger(__name__)
MAX_CORRECTABLE_FAILURES = 2
MAX_HISTORY_MESSAGES = 6


def _normalize_history(history: list[dict]) -> list[dict]:
    """Elimina solo mensajes vacíos y duplicados consecutivos exactos.

    No resume, reescribe ni elimina información semántica: la ventana, el TTL y
    el almacenamiento (``main.py``) permanecen intactos.
    """
    normalized: list[dict] = []
    for message in history:
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if normalized:
            previous = normalized[-1]
            if previous.get("role") == message.get("role") and previous.get("content") == content:
                continue
        normalized.append(message)
    return normalized


def _accepts_remaining_seconds(execute_tool: Callable[..., Awaitable[dict]]) -> bool:
    """Conserva mocks antiguos de 3 argumentos sin romper su firma."""
    try:
        parameters = inspect.signature(execute_tool).parameters
    except (TypeError, ValueError):
        return False
    return "remaining_seconds" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


async def run_agent_turn(
    provider,
    context: dict,
    message: str,
    history: list[dict],
    execute_tool: Callable[..., Awaitable[dict]],
    *,
    max_tool_calls: int,
    deadline_seconds: float = 25.0,
    deadline_at: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    wait_for: Callable[..., Awaitable] = asyncio.wait_for,
) -> tuple[LLMResult, list[dict]]:
    """Ejecuta el bucle tool-calling con un deadline absoluto compartido.

    `deadline_at` (instante monotonic) tiene prioridad sobre `deadline_seconds`
    y permanece inalterado durante toda la petición. El presupuesto restante se
    recalcula desde ese mismo instante antes de cada LLM y cada herramienta.

    LLM y herramientas se envuelven en `wait_for` con el restante global. La
    cancelación de Python detiene únicamente la espera local: no revierte
    procesamiento externo que Node.js ya haya recibido (p. ej., una escritura
    confirmada). Por eso cada herramienta se ejecuta una sola vez y no se
    reintenta.
    """
    recent_history = _normalize_history(
        history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    )
    results: list[dict] = []
    messages: list[dict] = [*recent_history, {"role": "user", "content": message}]
    executed = 0
    correctable_failures = 0
    started = clock()
    # Un solo anclaje: nunca se reinicia entre rondas LLM ni herramientas.
    ends = deadline_at if deadline_at is not None else started + deadline_seconds
    pass_remaining = _accepts_remaining_seconds(execute_tool)
    while True:
        remaining_global = ends - clock()
        if remaining_global <= 0:
            raise AgentError("AI_DEADLINE_EXCEEDED", "Global agent deadline exceeded", 504, True)
        allow_tools = executed < max_tool_calls
        try:
            current = await wait_for(
                provider.complete(context, messages, allow_tools=allow_tools),
                remaining_global,
            )
        except (asyncio.TimeoutError, TimeoutError) as error:
            raise AgentError(
                "AI_DEADLINE_EXCEEDED", "LLM call exceeded remaining global budget",
                504, True,
            ) from error
        if not current.tool_calls:
            break
        remaining = max_tool_calls - executed
        if remaining <= 0:
            break
        calls = current.tool_calls[:remaining]
        # La pertenencia a TOOLS solo prueba existencia, no autorización. El
        # conjunto anunciado por el selector debe conservarse hasta aquí: una
        # llamada registrada pero no autorizada (o devuelta sin schemas, con
        # allow_tools=False o por el fallback) se rechaza antes de ejecutarla.
        # El parser ya rechazó los nombres desconocidos y los JSON inválidos.
        authorized = set(select_tool_names(context, messages, allow_tools))
        for call in calls:
            if call.name in TOOLS and call.name not in authorized:
                raise AgentError(
                    "AI_TOOL_NOT_FOUND",
                    "LLM requested a tool that was not authorized for this request",
                    502,
                    details={
                        "reason": "TOOL_NOT_AUTHORIZED",
                        "tool": call.name,
                        "tool_call_id": call.id,
                    },
                )
        current.tool_calls = calls
        # Algunos proveidores omiten el id; así el mensaje `tool` lo referencia.
        for offset, call in enumerate(calls, start=1):
            if not call.id:
                call.id = f"call_{executed + offset}"
        messages.append(current.assistant_message())
        for call in calls:
            remaining_global = ends - clock()
            if remaining_global <= 0:
                raise AgentError("AI_DEADLINE_EXCEEDED", "No global budget for tool", 504, True)
            tool_started = clock()
            metrics = current_metrics()
            kwargs = {"remaining_seconds": remaining_global} if pass_remaining else {}
            # Cada tool_call se ejecuta una sola vez: los reintentos del
            # proveedor ocurren antes de esta llamada y nunca re-ejecutan tools
            # de escritura. wait_for acota la corrutina completa (no solo la
            # espera HTTP); al expirar cancela la espera local en Python, sin
            # revertir procesamiento externo ya recibido por Node.js.
            try:
                result = await wait_for(
                    execute_tool(call, context, message, **kwargs),
                    timeout=remaining_global,
                )
            except (asyncio.TimeoutError, TimeoutError) as error:
                raise AgentError(
                    "AI_DEADLINE_EXCEEDED", "Tool exceeded remaining global budget",
                    504, True,
                ) from error
            except AgentError as error:
                # Un error de validación no debe tumbar el turno: se devuelve al
                # modelo como resultado de la herramienta para que corrija el
                # dato o le pida al usuario la información que falta.
                if error.code not in CORRECTABLE_CODES or correctable_failures >= MAX_CORRECTABLE_FAILURES:
                    raise
                correctable_failures += 1
                result = {
                    "success": False,
                    "error": {
                        "code": error.code,
                        "message": error.public_message or error.message,
                        "details": error.details,
                    },
                }
            except Exception as error:
                logger.warning(
                    "tool_execution_failed requestId=%s tool=%s cause=%s",
                    request_id_context.get(), call.name, type(error).__name__,
                )
                raise AgentError(
                    "TOOL_EXECUTION_FAILED", "Unexpected tool execution failure", 502, False,
                    details={"tool": call.name, "cause": type(error).__name__},
                ) from error
            finally:
                if metrics:
                    metrics.tool_calls += 1
                    metrics.tool_latency_ms += (clock() - tool_started) * 1000
            executed += 1
            results.append(result)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False),
            })
            if ends - clock() <= 0:
                raise AgentError("AI_DEADLINE_EXCEEDED", "Global deadline exceeded", 504, True)
    return current, results
