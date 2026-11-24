from __future__ import annotations


from contextlib import asynccontextmanager
from collections import defaultdict
from dataclasses import field
from datetime import datetime, timezone
import json
import logging
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import deadline_chain, settings, validate_configuration
from .context import verify_context, verify_service_token
from .errors import AgentError, error_payload
from .agent import run_agent_turn
from .llm import get_provider
from .models import ChatRequest
from .observability import RequestMetrics, log_request_metrics, metrics_context, request_id_context
from .presentation import build_ui, sanitize_assistant_content
from .tools import execute_tool
from .conversation_store import ConversationStore
from .conversation_memory import ConversationMemory, TurnContext
from .decision_layer import decide, decision_metrics
from .direct_response import direct_response, direct_response_metrics, DirectResponseState
from .forensic import (
    correlation_id_context,
    set_correlation_id,
    get_correlation_id,
    get_request_id,
    log_request_received,
    log_history_loaded,
    log_conversation_memory_rebuilt,
    log_turn_context_resolved,
    log_decision_completed,
    log_direct_response_evaluated,
    log_direct_response_used,
    log_tool_selection_completed,
    log_conversation_turn_persisted,
    log_response_built,
    log_request_completed,
    log_request_failed,
    categorize_error,
    StageTimer,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("campusvote-agent-python")
provider = get_provider()
conversation_store: ConversationStore | None = None
conversation_memories: dict[str, ConversationMemory] = {}


def conversation_key(conversation_id: str | None, context: dict) -> str | None:
    """Clave aislada por usuario, organización, sede y conversación.

    ``organization_id`` y ``site_id`` se toman exclusivamente del contexto
    verificado (JWT RS256), nunca del cuerpo de la solicitud. Si falta alguno,
    se devuelve ``None`` para fallar cerrado sin acceder a Redis.

    Migración: las claves antiguas ``sub:org:conv`` no se leen ni se migran
    (no hay fallback inseguro entre esquemas). Se dejan expirar por el TTL de
    conversación configurado en ``AI_CONVERSATION_TTL_SECONDS`` (1800 s por
    defecto), tras lo cual Redis las elimina automáticamente.
    """
    if not conversation_id:
        return None
    sub = context.get("sub")
    organization_id = context.get("organization_id")
    site_id = context.get("site_id")
    if not all(isinstance(value, str) and value for value in (sub, organization_id, site_id)):
        return None
    return f"{sub}:{organization_id}:{site_id}:{conversation_id}"


async def get_history(conversation_id: str | None, context: dict) -> list[dict]:
    """Recupera historial para el LLM (solo role + content)."""
    key = conversation_key(conversation_id, context)
    if not key or conversation_store is None:
        return []
    return await conversation_store.get_history(key)


async def save_turn(
    conversation_id: str | None,
    context: dict,
    user_message: str,
    assistant_message: str,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    entities: dict[str, str] | None = None,
    pending_operation: dict | None = None,
    pending_proposal: dict | None = None,
    confirmation_state: str = "none",
) -> None:
    """Guarda turno enriquecido en el almacén persistente y actualiza memoria."""
    key = conversation_key(conversation_id, context)
    if not key or conversation_store is None:
        return

    # Guardar en almacén persistente (con tool_calls, tool_results, entities y estado)
    await conversation_store.append_turn(
        key,
        user_message,
        assistant_message,
        tool_calls,
        tool_results,
        entities,
        pending_operation=pending_operation,
        pending_proposal=pending_proposal,
        confirmation_state=confirmation_state,
    )

    # Actualizar memoria conversacional (Nivel 2 y 3)
    memory = conversation_memories.get(key)
    if memory is None:
        memory = ConversationMemory()
        conversation_memories[key] = memory

    memory.start_turn()
    memory.end_turn(user_message, assistant_message, tool_calls, tool_results, entities)
    memory.session.pending_operation = pending_operation
    memory.session.pending_proposal = pending_proposal
    memory.session.confirmation_state = confirmation_state


async def get_or_create_memory(conversation_id: str | None, context: dict) -> ConversationMemory | None:
    """Obtiene la memoria de una conversación, reconstruyéndola desde el historial si es necesario."""
    key = conversation_key(conversation_id, context)
    if not key or conversation_store is None:
        return None

    # Si ya está en memoria, devolverla
    if key in conversation_memories:
        return conversation_memories[key]

    # Reconstruir desde el historial completo persistido
    full_history = await conversation_store.get_full_history(key)
    if full_history:
        memory = ConversationMemory.rebuild_from_history(full_history)
        conversation_memories[key] = memory
        return memory

    # No hay historial, crear memoria nueva
    memory = ConversationMemory()
    conversation_memories[key] = memory
    return memory


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_configuration()
    global conversation_store
    conversation_store = await ConversationStore.create(
        max_messages=settings.conversation_max_messages,
        ttl_seconds=settings.conversation_ttl_seconds,
    )
    # Log which backend is active
    if conversation_store.redis:
        # Mask password in URL if present
        redis_url = settings.redis_url
        if redis_url and "@" in redis_url:
            # Format: redis://:password@host:port or redis://user:password@host:port
            parts = redis_url.split("@")
            masked = "redis://***@" + parts[-1]
        else:
            masked = redis_url
        logger.info("conversation_store_backend_active backend=redis url=%s", masked)
    else:
        logger.warning("conversation_store_backend_active backend=memory_fallback reason=redis_not_configured")
    logger.info(
        "deadline_chain_validated requestId=- chain=%s",
        json.dumps(deadline_chain(), sort_keys=True),
    )
    yield
    conversation_store = None


app = FastAPI(title="CampusVote Agent AI", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AgentError)
async def agent_error_handler(request: Request, error: AgentError):
    correlation = getattr(request.state, "request_id", None)
    logger.warning("request_failed service=agent-python requestId=%s status=%s code=%s cause=%s",
                   correlation, error.status, error.code, type(error).__name__)
    return JSONResponse(status_code=error.status, content=error_payload(error, correlation))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError):
    correlation = getattr(request.state, "request_id", None)
    # Log solo ubicación y tipo: nunca el valor recibido.
    logger.warning(
        "request_validation_failed service=agent-python requestId=%s errors=%s",
        correlation,
        [{"loc": list(item.get("loc", ())), "type": item.get("type")} for item in error.errors()],
    )
    safe = AgentError("VALIDATION_INVALID_REQUEST", "Revisa la información ingresada.", 422)
    return JSONResponse(status_code=422, content=error_payload(safe, correlation))


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, error: HTTPException):
    correlation = getattr(request.state, "request_id", None)
    code, message = {
        401: ("AI_SERVICE_UNAUTHORIZED", "No fue posible validar la solicitud del asistente."),
        403: ("AI_SCOPE_FORBIDDEN", "No tienes permisos para realizar esta operación."),
        404: ("AI_RESOURCE_NOT_FOUND", "No se encontró el recurso solicitado."),
        429: ("AI_RATE_LIMITED", "Demasiadas solicitudes. Inténtalo más tarde."),
    }.get(error.status_code, ("AI_INTERNAL_ERROR", "Ocurrió un problema inesperado."))
    safe = AgentError(code, message, error.status_code)
    return JSONResponse(status_code=error.status_code, content=error_payload(safe, correlation))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception):
    correlation = getattr(request.state, "request_id", None)
    logger.error("request_failed service=agent-python requestId=%s status=500 code=AI_INTERNAL_ERROR cause=%s",
                 correlation, type(error).__name__)
    safe = AgentError("AI_INTERNAL_ERROR", "Ocurrió un problema inesperado.", 500)
    return JSONResponse(status_code=500, content=error_payload(safe, correlation))


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Correlation-Id", "")
    request_id = supplied if supplied and len(supplied) <= 128 and all(c.isalnum() or c in "._:-" for c in supplied) else __import__("uuid").uuid4().hex
    request.state.request_id = request_id
    context_token = request_id_context.set(request_id)
    metrics_token = metrics_context.set(RequestMetrics(request_id=request_id))
    started = time.monotonic()
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Correlation-Id"] = request_id
        logger.info("request_complete service=agent-python requestId=%s method=%s route=%s status=%s durationMs=%s",
                    request_id, request.method, request.url.path, response.status_code,
                    round((time.monotonic() - started) * 1000))
        return response
    finally:
        log_request_metrics()
        metrics_context.reset(metrics_token)
        request_id_context.reset(context_token)


@app.get("/health")
async def health():
    # Check if conversation store (Redis) is initialized and responsive
    if conversation_store is None:
        raise HTTPException(status_code=503, detail={"success": False, "service": "campusvote-agent-python", "reason": "store_not_initialized"})
    backend = "redis" if conversation_store.redis else "memory_fallback"
    if conversation_store.redis:
        try:
            await conversation_store.redis.ping()
        except Exception:
            raise HTTPException(status_code=503, detail={"success": False, "service": "campusvote-agent-python", "reason": "redis_unavailable", "backend": backend})
    # Mask password and host ID in URL if present
    redis_url = settings.redis_url
    if redis_url:
        # Parse and mask sensitive parts: password and host ID
        # Format: redis://[:password@]host:port
        # Mask host ID: keep first 3 chars and last 3 chars, replace middle with ***
        try:
            from urllib.parse import urlparse
            parsed = urlparse(redis_url)
            host = parsed.hostname or ""
            # Mask middle of host ID (Render internal host format: red-xxxxxxxxxxxx)
            if len(host) > 8:
                masked_host = host[:4] + "***" + host[-3:]
            else:
                masked_host = "***" + host[-3:]
            masked = f"redis://{masked_host}:{parsed.port or 6379}"
        except Exception:
            masked = "redis://***:6379"
    else:
        masked = "not_configured"
    return {"success": True, "service": "campusvote-agent-python", "backend": backend, "redis_url": masked}


def absolute_deadline(deadline_at: str) -> float:
    """Convierte `deadline_at` (ISO UTC) a instante absoluto de `time.monotonic()`.

    Captura ambas referencias de reloj en una sola evaluación, dentro de esta
    función: el tiempo entre medir el restante y anclar el reloj no puede
    ampliar el presupuesto. Nunca amplía el deadline recibido; lo acota a
    min(presupuesto_configurado, restante_real).
    """
    try:
        deadline = datetime.fromisoformat(str(deadline_at).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise AgentError(
            "AI_DEADLINE_EXCEEDED", "Invalid request deadline", 504, True,
            details={"reason": "INVALID_DEADLINE_AT"},
        ) from error
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    # monotonic primero: cualquier delta existente hace el anclaje conservador.
    mono_now, wall_now = time.monotonic(), datetime.now(timezone.utc)
    remaining = (deadline - wall_now).total_seconds()
    if remaining <= 0:
        raise AgentError(
            "AI_DEADLINE_EXCEEDED", "Request deadline already exceeded", 504, True,
            details={"remaining_seconds": round(remaining, 2)},
        )
    return mono_now + min(settings.agent_deadline_seconds, remaining)


@app.post("/v1/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_ai_context: str | None = Header(default=None),
    x_correlation_id: str | None = Header(default=None),
):
    # A. Recepción: configuración de correlación y validación inicial
    corr_token = set_correlation_id(payload.correlation_id)
    request_id = get_request_id()
    
    verify_service_token(authorization)
    context = verify_context(x_ai_context)
    
    # Log recepción
    log_request_received("/v1/chat", "valid", list(context.keys()))
    
    # Un único instante absoluto compartido por LLM y herramientas.
    agent_deadline_at = absolute_deadline(context["deadline_at"])
    if x_correlation_id != payload.correlation_id or x_correlation_id != context["correlation_id"]:
        log_request_failed(
            total_duration_ms=0,
            stage_durations={},
            error_code="AI_INVALID_REQUEST",
            error_category=categorize_error("AI_INVALID_REQUEST"),
        )
        raise AgentError("AI_INVALID_REQUEST", "Correlation id mismatch", 400)
    context["_raw_token"] = x_ai_context
    context["conversation_id"] = payload.conversation_id
    
    # Timers para medición de etapas
    stage_timers = {
        "history_load": StageTimer("history_load"),
        "memory_rebuild": StageTimer("memory_rebuild"),
        "turn_context": StageTimer("turn_context"),
        "decision_layer": StageTimer("decision_layer"),
        "direct_response": StageTimer("direct_response"),
        "agent_turn": StageTimer("agent_turn"),
        "persistence": StageTimer("persistence"),
    }
    
    try:
        # B. Historial/Memoria
        history = await get_history(payload.conversation_id, context)
        stage_timers["history_load"] = StageTimer("history_load")  # Reset after await
        log_history_loaded(len(history))
        
        memory = await get_or_create_memory(payload.conversation_id, context)
        stage_timers["memory_rebuild"] = StageTimer("memory_rebuild")
        # Contar turnos reconstruidos aproximado
        turn_count = len([m for m in (await get_history(payload.conversation_id, context)) if m.get("role") == "user"])
        log_conversation_memory_rebuilt(turn_count)
        
        entities = {}
        if memory:
            entities = memory.extract_entities_from_message(payload.message)
        
        # Resolución determinista ANTES de la decisión del agente
        turn_context = memory.resolve_turn_context(payload.message) if memory else TurnContext()
        stage_timers["turn_context"] = StageTimer("turn_context")
        context["conversation_context"] = turn_context.to_dict()
        log_turn_context_resolved(
            resolved_reference_count=len(turn_context.resolved),
            ambiguous_reference_count=len(turn_context.ambiguous),
            selected_entity_count=len(turn_context.selected),
            last_query_fresh=bool(turn_context.last_query and turn_context.last_query.get("fresh")),
            last_query_tool=turn_context.last_query.get("tool") if turn_context.last_query else None,
        )
        
        # Computar clave de conversación una vez para reutilizarla
        key = conversation_key(payload.conversation_id, context)
        
        # C. Decision Layer
        stage_timers["decision_layer"] = StageTimer("decision_layer")
        decision = decide(
            payload.message,
            turn_context,
            history_available=bool(history),
        )
        context["decision"] = decision.to_dict()
        logger.info(json.dumps(
            decision_metrics(decision, request_id_context.get()), ensure_ascii=False
        ))
        log_decision_completed(
            decision_category=decision.category.value,
            decision_reason=decision.reason,
            allow_tools=decision.allow_tools,
            needs_clarification=decision.needs_clarification,
            selected_tool_count=0,  # Se actualiza tras selección de herramientas
        )
        
        # D. Direct Response Layer
        stage_timers["direct_response"] = StageTimer("direct_response")
        direct = direct_response(
            payload.message,
            decision,
            turn_context,
            memory,
            history,
        )
        logger.info(json.dumps(
            direct_response_metrics(direct, request_id_context.get()), ensure_ascii=False
        ))
        log_direct_response_evaluated(
            direct_response_state=direct.state.value if direct else None,
            direct_response_reason=direct.reason if direct else None,
            direct_response_source=direct.source if direct else None,
            llm_skipped=direct.state == DirectResponseState.ANSWERED if direct else False,
            tool_execution_skipped=direct.state == DirectResponseState.ANSWERED if direct else False,
        )
        
        if direct is not None and direct.state == DirectResponseState.ANSWERED:
            log_direct_response_used(
                direct_response_state=direct.state.value,
                direct_response_reason=direct.reason,
                direct_response_source=direct.source,
            )
        
        if direct is not None and direct.state == DirectResponseState.ANSWERED:
            # Respuesta directa: omitir LLM y herramientas completamente
            reply_content = direct.text
            tool_calls_serializable = None
            tool_results_serializable = []
            pending_operation = None
            confirmation_state = "none"
            class _DirectCurrent:
                content = ""
                tool_calls = []
                truncated = False
            current = _DirectCurrent()
            current.content = direct.text
            results = []
        else:
            # E. Selección de herramientas (ocurre dentro de run_agent_turn/provider)
            # F. LLM request/response (dentro de provider)
            # G. Validación de herramientas (dentro de agent.py)
            # H. Ejecución de herramientas (dentro de agent.py)
            stage_timers["agent_turn"] = StageTimer("agent_turn")
            current, results = await run_agent_turn(
                provider,
                context,
                payload.message,
                history,
                execute_tool,
                max_tool_calls=settings.max_tool_calls,
                deadline_at=agent_deadline_at,
                turn_context=turn_context,
                decision=decision,
            )
        
        last_result = results[-1] if results and isinstance(results[-1], dict) else None
        last_action = last_result.get("data") if last_result else None
        last_tool = last_result.get("tool") if last_result else None
        list_tools = {
            "getSiteFairs", "getSiteProjects", "getSiteStands", "getSiteResults",
            "listTeachingCriteria", "listFairStands", "listFairCategories", "listFairCategoryRubrics"
        }
        tool_data = last_result.get("data") if last_result and last_tool in list_tools else None
        reply_content = (current.content or "").strip()
        if not reply_content:
            if isinstance(last_action, dict) and last_action.get("status") == "confirmation_required":
                reply_content = "Preparé la propuesta con los datos indicados. Revísala y confirma para aplicarla."
            elif last_result and last_result.get("success") is False:
                reason = (last_result.get("error") or {}).get("message")
                reply_content = (
                    f"No pude completar la operación: {reason}"
                    if isinstance(reason, str) and reason.strip()
                    else "No pude completar la operación con esos datos. Revísalos e inténtalo de nuevo."
                )
            else:
                raise AgentError("AI_RESPONSE_INVALID", "LLM returned no content", 502)
        if isinstance(last_action, dict) and last_action.get("status") == "confirmation_required" and last_action.get("tool") == "createFair":
            reply_content = (
                "Revisa los datos de la feria en la tarjeta. Sube la portada desde aquí; "
                "podrás confirmar cuando esté lista."
            )
        # Última barrera: si el modelo volcó un JSON técnico, la conversación recibe
        # un resumen conversacional en lugar del payload del backend.
        reply_content = sanitize_assistant_content(reply_content)[:4000]

        # Extraer tool_calls serializables
        tool_calls_serializable = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in current.tool_calls
        ] if current.tool_calls else None

        # Extraer tool_results con nombre de herramienta
        tool_results_serializable = []
        for i, result in enumerate(results):
            if isinstance(result, dict):
                tool_name = None
                if i < len(current.tool_calls):
                    tool_name = current.tool_calls[i].name
                tool_results_serializable.append({
                    "tool": tool_name,
                    "success": result.get("success"),
                    "data": result.get("data"),
                    "error": result.get("error"),
                })

        # Estado de confirmación derivado del resultado real del turno
        pending_operation = None
        confirmation_state = "none"
        if isinstance(last_action, dict) and last_action.get("status") == "confirmation_required":
            pending_operation = {
                "tool": last_action.get("tool") or last_tool,
                "operation_id": last_action.get("operation_id"),
                "version": last_action.get("version"),
            }
            confirmation_state = "awaiting_confirmation"

        # I. Persistencia y respuesta
        stage_timers["persistence"] = StageTimer("persistence")
        # Obtener pending_proposal de la memoria si existe
        memory = conversation_memories.get(key)
        pending_proposal = memory.session.pending_proposal if memory else None
        await save_turn(
            payload.conversation_id,
            context,
            payload.message,
            reply_content,
            tool_calls=tool_calls_serializable,
            tool_results=tool_results_serializable,
            entities=entities if entities else None,
            pending_operation=pending_operation,
            pending_proposal=pending_proposal,
            confirmation_state=confirmation_state,
        )
        log_conversation_turn_persisted(
            message_count=2,  # user + assistant
            has_tool_calls=bool(tool_calls_serializable),
            pending_operation=bool(pending_operation),
            confirmation_state=confirmation_state,
        )
        
        ui, operation = build_ui(last_action)
        log_response_built(
            has_ui=bool(ui and ui.get("type") != "message"),
            has_operation=operation is not None,
            tool_calls_count=len(results),
            is_direct_response=direct is not None and direct.state == DirectResponseState.ANSWERED,
        )
        
        # Recopilar duraciones de etapas
        stage_durations = {
            name: timer.elapsed_ms() for name, timer in stage_timers.items()
        }
        
        outcome = "direct_response" if (direct is not None and direct.state == DirectResponseState.ANSWERED) else "agent_response"
        log_request_completed(
            total_duration_ms=sum(stage_durations.values()),
            stage_durations=stage_durations,
            outcome=outcome,
        )
        
        return {
            "success": True,
            "conversation_id": payload.conversation_id,
            "message": {"role": "assistant", "content": reply_content},
            "ui": ui,
            "operation": operation,
            "citations": [],
            "suggested_actions": [],
            "meta": {"correlation_id": payload.correlation_id, "tool_calls": len(results), "truncated": bool(current.truncated), "last_tool": last_tool, "tool_data": tool_data},
        }
    except AgentError as e:
        stage_durations = {name: timer.elapsed_ms() for name, timer in stage_timers.items()}
        log_request_failed(
            total_duration_ms=sum(stage_durations.values()),
            stage_durations=stage_durations,
            error_code=e.code,
            error_category=categorize_error(e.code),
        )
        raise
    except Exception as e:
        stage_durations = {name: timer.elapsed_ms() for name, timer in stage_timers.items()}
        log_request_failed(
            total_duration_ms=sum(stage_durations.values()),
            stage_durations=stage_durations,
            error_code="AI_INTERNAL_ERROR",
            error_category=categorize_error("AI_INTERNAL_ERROR"),
        )
        raise
    finally:
        correlation_id_context.reset(corr_token)
