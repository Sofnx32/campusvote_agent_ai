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
from .deterministic_responses import build_deterministic_response
from .providers.tool_registry import TOOLS
from .providers.tool_selector import (
    CATEGORY_RUBRIC_DOMAIN_TOOLS,
    select_tool_names,
)
from .config import settings
from .forensic import (
    log_llm_invalid_response,
    log_tool_selection_completed,
    log_tool_validation_started,
    log_tool_validation_passed,
    log_tool_validation_rejected,
    log_tool_execution_started,
    log_tool_execution_completed,
    log_tool_execution_failed,
    categorize_error,
)

logger = logging.getLogger(__name__)
MAX_CORRECTABLE_FAILURES = 2

# Errores que pueden significar "no hubo texto final" y que, cuando ya se
# ejecutó una lectura con éxito, se sustituyen por una respuesta determinista.
RECOVERABLE_LLM_ERROR_CODES = frozenset({
    "AI_RESPONSE_INVALID",
    "LLM_INVALID_RESPONSE",
    "LLM_OUTPUT_TOKEN_LIMIT",
})

# Herramientas del dominio categorías/rúbricas cuya invocación no debe romper
# el turno aunque el selector no las haya anunciado en este ciclo.
RECOVERABLE_DOMAIN_TOOLS = CATEGORY_RUBRIC_DOMAIN_TOOLS | frozenset({
    "listFairCategoryRubrics",
    "updateFairCategory",
    "deleteFairCategory",
})


def _invalid_response_reason(error: AgentError, has_tool_results: bool) -> str:
    """Clasifica el error en el vocabulario cerrado de razones forenses."""
    if error.code == "LLM_OUTPUT_TOKEN_LIMIT":
        return "finish_reason"
    return "empty_content_after_tools" if has_tool_results else "empty_content_no_tools"


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
        log_tool_validation_rejected(
            validation_error_code="TOOL_NOT_ALLOWED",
            tool_name=call.name,
            tool_call_index=executed,
            allowed_tools=sorted(allowed_tools) if allowed_tools else [],
        )
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
        log_tool_validation_rejected(
            validation_error_code="TOOL_NOT_ALLOWED",
            tool_name=call.name,
            tool_call_index=executed,
            allowed_tools=[],
        )
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
    executed_tool_names: list[str] = []
    messages: list[dict] = [*recent_history, {"role": "user", "content": message}]
    executed = 0
    correctable_failures = 0
    recovery_used = False
    started = clock()
    # Un solo anclaje: nunca se reinicia entre rondas LLM ni herramientas.
    ends = deadline_at if deadline_at is not None else started + deadline_seconds
    pass_remaining = _accepts_remaining_seconds(execute_tool)

    async def _call_provider(announce_tools: bool) -> LLMResult:
        """Llama al proveedor y recupera respuestas vacías con evidencia previa."""
        try:
            return await wait_for(
                provider.complete(effective_context, messages, allow_tools=announce_tools),
                max(ends - clock(), 0.001),
            )
        except (asyncio.TimeoutError, TimeoutError) as error:
            raise AgentError(
                "AI_DEADLINE_EXCEEDED", "LLM call exceeded remaining global budget",
                504, True,
            ) from error
        except AgentError as error:
            announced = select_tool_names(effective_context, messages, announce_tools) if announce_tools else []
            reason = _invalid_response_reason(error, bool(results))
            if error.code in RECOVERABLE_LLM_ERROR_CODES and results:
                text = build_deterministic_response(results, executed_tool_names, effective_context)
                if text:
                    log_llm_invalid_response(
                        reason,
                        selected_tool_names=announced,
                        error_code=error.code,
                        outcome="recovered",
                    )
                    return LLMResult(content=text, allowed_tools=frozenset())
            log_llm_invalid_response(
                reason,
                selected_tool_names=announced,
                error_code=error.code,
            )
            raise

    while True:
        remaining_global = ends - clock()
        if remaining_global <= 0:
            raise AgentError("AI_DEADLINE_EXCEEDED", "Global agent deadline exceeded", 504, True)
        # El Decision Layer puede acotar herramientas, nunca ampliarlas: la
        # comprobación existente de presupuesto y el selector siguen vigentes.
        allow_tools = executed < max_tool_calls
        if decision is not None:
            allow_tools = decision.allow_tools and allow_tools
        current = await _call_provider(allow_tools)
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

        # Recuperación: el modelo puede pedir una herramienta válida del dominio
        # que el selector no anunció. En lugar de reventar con TOOL_NOT_ALLOWED,
        # se amplía el anuncio y se reintenta una sola vez.
        unselected = [
            call.name for call in calls
            if call.name in RECOVERABLE_DOMAIN_TOOLS
            and (not cycle_allowed or call.name not in cycle_allowed)
        ]
        # Solo se recupera si hubo un anuncio filtrado por dominio: cuando no se
        # anunció ninguna herramienta (conversación general / allow_tools=False)
        # el rechazo estricto TOOL_NOT_ALLOWED se mantiene intacto.
        if cycle_allowed and unselected and not recovery_used and decision_allow:
            recovery_used = True
            log_llm_invalid_response(
                f"unselected_tool:{unselected[0]}",
                selected_tool_names=sorted(cycle_allowed),
                error_code="LLM_INVALID_RESPONSE",
                outcome="recovered",
            )
            effective_context["force_tool_names"] = sorted(
                cycle_allowed | RECOVERABLE_DOMAIN_TOOLS
            )
            current = await _call_provider(True)
            cycle_allowed = current.allowed_tools if isinstance(current.allowed_tools, frozenset) else frozenset()
            calls = current.tool_calls[:remaining]
            if not calls:
                break

        # E. Selección de herramientas (log)
        if calls:
            log_tool_selection_completed(
                selected_tool_count=len(calls),
                selected_tool_names=[c.name for c in calls],
                selection_reason="llm_generated",
                allowed_tool_count=len(cycle_allowed) if cycle_allowed else 0,
            )
        
        for call in calls:
            log_tool_validation_started(
                tool_name=call.name,
                tool_call_index=executed,
                allowed_tool_count=len(cycle_allowed) if cycle_allowed else 0,
            )
            _validate_tool_call(
                call=call,
                allowed_tools=cycle_allowed,
                executed=executed,
                max_tool_calls=max_tool_calls,
                decision_allow_tools=decision_allow,
            )
            log_tool_validation_passed(
                tool_name=call.name,
                tool_call_index=executed,
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
            
            log_tool_execution_started(
                tool_name=call.name,
                tool_call_index=executed,
                tool_call_id=call.id,
            )
            try:
                result = await wait_for(
                    execute_tool(call, context, message, **kwargs),
                    timeout=remaining_global,
                )
            except (asyncio.TimeoutError, TimeoutError) as error:
                log_tool_execution_failed(
                    tool_name=call.name,
                    tool_call_index=executed,
                    tool_call_id=call.id,
                    duration_ms=(clock() - tool_started) * 1000,
                    error_type="timeout",
                    error_code="AI_DEADLINE_EXCEEDED",
                )
                raise AgentError(
                    "AI_DEADLINE_EXCEEDED", "Tool exceeded remaining global budget",
                    504, True,
                ) from error
            except AgentError as error:
                # Un error de validación no debe tumbar el turno: se devuelve al
                # modelo como resultado de la herramienta para que corrija el
                # dato o le pida al usuario la información que falta.
                if error.code not in CORRECTABLE_CODES or correctable_failures >= MAX_CORRECTABLE_FAILURES:
                    log_tool_execution_failed(
                        tool_name=call.name,
                        tool_call_index=executed,
                        tool_call_id=call.id,
                        duration_ms=(clock() - tool_started) * 1000,
                        error_type="agent_error",
                        error_code=error.code,
                    )
                    raise
                correctable_failures += 1
                log_tool_execution_completed(
                    tool_name=call.name,
                    tool_call_index=executed,
                    tool_call_id=call.id,
                    duration_ms=(clock() - tool_started) * 1000,
                    functional_status="error",
                    error_code=error.code,
                )
                result = {
                    "success": False,
                    "error": {
                        "code": error.code,
                        "message": error.public_message or error.message,
                        "details": error.details,
                    },
                }
            except Exception as error:
                log_tool_execution_failed(
                    tool_name=call.name,
                    tool_call_index=executed,
                    tool_call_id=call.id,
                    duration_ms=(clock() - tool_started) * 1000,
                    error_type="unexpected",
                    error_code="TOOL_EXECUTION_FAILED",
                )
                logger.warning(
                    "tool_execution_failed requestId=%s tool=%s cause=%s",
                    request_id_context.get(), call.name, type(error).__name__,
                )
                raise AgentError(
                    "TOOL_EXECUTION_FAILED", "Unexpected tool execution failure", 502, False,
                    details={"tool": call.name, "cause": type(error).__name__},
                ) from error
            else:
                functional_status = "success"
                if isinstance(result, dict):
                    if result.get("success") is False:
                        functional_status = "error"
                    if isinstance(result.get("data"), dict) and result["data"].get("status") == "confirmation_required":
                        functional_status = "confirmation_required"
                log_tool_execution_completed(
                    tool_name=call.name,
                    tool_call_index=executed,
                    tool_call_id=call.id,
                    duration_ms=(clock() - tool_started) * 1000,
                    functional_status=functional_status,
                    error_code=result.get("error", {}).get("code") if isinstance(result, dict) and not result.get("success") else None,
                )
            finally:
                if metrics:
                    metrics.tool_calls += 1
                    metrics.tool_latency_ms += (clock() - tool_started) * 1000
            executed += 1
            results.append(result)
            executed_tool_names.append(call.name)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False),
            })
            if ends - clock() <= 0:
                raise AgentError("AI_DEADLINE_EXCEEDED", "Global deadline exceeded", 504, True)
    return current, results
