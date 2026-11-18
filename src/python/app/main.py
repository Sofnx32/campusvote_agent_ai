from __future__ import annotations


from contextlib import asynccontextmanager
from collections import defaultdict
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

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("campusvote-agent-python")
provider = get_provider()
conversation_history: dict[str, list[dict]] = defaultdict(list)
conversation_touched: dict[str, datetime] = {}


def conversation_key(conversation_id: str | None, context: dict) -> str | None:
    if not conversation_id:
        return None
    return f"{context['sub']}:{context['organization_id']}:{conversation_id}"


def get_history(conversation_id: str | None, context: dict) -> list[dict]:
    key = conversation_key(conversation_id, context)
    if not key:
        return []
    touched = conversation_touched.get(key)
    if not touched or (datetime.now(timezone.utc) - touched).total_seconds() > settings.conversation_ttl_seconds:
        conversation_history.pop(key, None)
        conversation_touched.pop(key, None)
        return []
    return list(conversation_history[key])


def save_turn(
    conversation_id: str | None,
    context: dict,
    user_message: str,
    assistant_message: str,
) -> None:
    key = conversation_key(conversation_id, context)
    if not key:
        return
    history = conversation_history[key]
    history.extend(
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
    )
    del history[:-settings.conversation_max_messages]
    conversation_touched[key] = datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_configuration()
    logger.info(
        "deadline_chain_validated requestId=- chain=%s",
        json.dumps(deadline_chain(), sort_keys=True),
    )
    yield


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
    return {"success": True, "service": "campusvote-agent-python"}


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
    verify_service_token(authorization)
    context = verify_context(x_ai_context)
    # Un único instante absoluto compartido por LLM y herramientas.
    agent_deadline_at = absolute_deadline(context["deadline_at"])
    if x_correlation_id != payload.correlation_id or x_correlation_id != context["correlation_id"]:
        raise AgentError("AI_INVALID_REQUEST", "Correlation id mismatch", 400)
    context["_raw_token"] = x_ai_context
    context["conversation_id"] = payload.conversation_id
    history = get_history(payload.conversation_id, context)
    current, results = await run_agent_turn(
        provider,
        context,
        payload.message,
        history,
        execute_tool,
        max_tool_calls=settings.max_tool_calls,
        deadline_at=agent_deadline_at,
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
    save_turn(payload.conversation_id, context, payload.message, reply_content)
    ui, operation = build_ui(last_action)
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
