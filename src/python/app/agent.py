from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import FrozenSet

from .errors import AgentError, CORRECTABLE_CODES
from .models import LLMResult, ToolCall
from .observability import current_metrics, request_id_context
from .conversation_memory import TurnContext
from .decision_layer import Decision
from .providers.tool_registry import TOOLS
from .providers.tool_selector import select_tool_names
from .config import settings

logger = logging.getLogger(__name__)
MAX_CORRECTABLE_FAILURES = 2


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


def _validate_tool_call(
    call: ToolCall,
    allowed_tools: FrozenSet[str],
    executed: int,
    max_tool_calls: int,
    decision_allow_tools: bool,
) -> None:
    """Validación centralizada pre-ejecución de un tool_call.

    Diferencia códigos de error según la causa:
    - TOOL_NOT_FOUND: no existe en el registro canónico (TOOLS)
    - TOOL_NOT_ALLOWED: existe en TOOLS pero no en allowlist del ciclo actual
    - TOOLS_DISABLED: Decision Layer deshabilitó herramientas (allow_tools=False)
    - TOOL_LIMIT_EXCEEDED: supera max_tool_calls configurado
    - INVALID_TOOL_CALL: estructura inválida (parser)

    Nunca convierte errores en éxitos silenciosos.
    """
    # 1. Existencia en registro canónico
    if call.name not in TOOLS:
        raise AgentError(
            "TOOL_NOT_FOUND",
            f"Tool '{call.name}' is not registered",
            404,
            details={"reason": "TOOL_NOT_FOUND", "tool": call.name, "tool_call_id": call.id},
        )

    # 2. Decision Layer: herramientas deshabilitadas para el turno
    if not decision_allow_tools:
        raise AgentError(
            "TOOLS_DISABLED",
            "Tools are disabled for this turn by Decision Layer",
            403,
            details={"reason": "TOOLS_DISABLED", "tool": call.name, "tool_call_id": call.id},
        )

    # 3. Límite global de herramientas por turno
    if executed >= max_tool_calls:
        raise AgentError(
            "TOOL_LIMIT_EXCEEDED",
            f"Tool call limit ({max_tool_calls}) exceeded",
            429,
            details={"reason": "TOOL_LIMIT_EXCEEDED", "tool": call.name, "tool_call_id": call.id, "limit": max_tool_calls},
        )

    # 4. Allowlist inmutable del ciclo LLM actual
    # Si el LLM devolvió tool_calls pero allow_tools=False (o sin schemas),
    # allowed_tools será frozenset() -> rechazo con TOOL_NOT_ALLOWED
    if allowed_tools and call.name not in allowed_tools:
        raise AgentError(
            "TOOL_NOT_ALLOWED",
            f"Tool '{call.name}' not in allowlist for this LLM cycle",
            403,
            details={
                "reason": "TOOL_NOT_ALLOWED",
                "tool": call.name,
                "tool_call_id": call.id,
                "allowed_tools": sorted(allowed_tools) if allowed_tools else [],
            },
        )
    # Si allowed_tools está vacío (frozenset()) pero decision_allow_tools=True,
    # significa que el LLM devolvió tool_calls cuando no se anunciaron schemas.
    # El parser ya validó existencia en TOOLS, pero no hay autorización -> TOOL_NOT_ALLOWED
    if not allowed_tools and decision_allow_tools:
        raise AgentError(
            "TOOL_NOT_ALLOWED",
            "Tools were not announced for this LLM cycle (allow_tools=True but no schemas sent)",
            403,
            details={"reason": "TOOL_NOT_ALLOWED", "tool": call.name, "tool_call_id": call.id, "allowed_tools": []},
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
    turn_context: TurnContext | None = None,
    decision: Decision | None = None,
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

    Cada ciclo LLM construye su propia allowlist inmutable (frozenset) que se
    propaga: LLMResult.allowed_tools -> ToolCall.allowed_tools -> validación.
    """
    max_hist = getattr(settings, "max_history_messages", 12)
    recent_history = _normalize_history(
        history[-max_hist:] if len(history) > max_hist else history
    )
    # El contexto conversacional se expone al proveedor y al selector como un
    # diccionario acotado, sin tocar `messages`, los prompts ni el contrato HTTP.
    effective_context = dict(context)
    if turn_context is not None:
        effective_context["conversation_context"] = turn_context.to_dict()
    if decision is not None:
        effective_context["decision"] = decision.to_dict()
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
        # El Decision Layer puede acotar herramientas, nunca ampliarlas: la
        # comprobación existente de presupuesto y el selector siguen vigentes.
        allow_tools = executed < max_tool_calls
        if decision is not None:
            allow_tools = decision.allow_tools and allow_tools
        try:
            current = await wait_for(
                provider.complete(effective_context, messages, allow_tools=allow_tools),
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

        # Validación centralizada pre-ejecución usando allowlist propagada
        # current.allowed_tools es la allowlist inmutable del ciclo LLM actual
        cycle_allowed = current.allowed_tools if isinstance(current.allowed_tools, frozenset) else frozenset()
        decision_allow = decision.allow_tools if decision else True
        for call in calls:
            _validate_tool_call(
                call=call,
                allowed_tools=cycle_allowed,
                executed=executed,
                max_tool_calls=max_tool_calls,
                decision_allow_tools=decision_allow,
            )

        current.tool_calls = calls
        # Algunos proveedores omiten el id; así el mensaje `tool` lo referencia.
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
