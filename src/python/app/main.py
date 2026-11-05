from __future__ import annotations


from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime, timezone
import logging
import time

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .config import settings
from .context import verify_context, verify_service_token
from .errors import AgentError, error_payload
from .llm import get_provider
from .llm import CONFIRMATION
from .models import ChatRequest
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
    yield


app = FastAPI(title="CampusVote Agent AI", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AgentError)
async def agent_error_handler(request: Request, error: AgentError):
    correlation = request.headers.get("X-Correlation-Id")
    return JSONResponse(status_code=error.status, content=error_payload(error, correlation))


@app.get("/health")
async def health():
    return {"success": True, "service": "campusvote-agent-python"}


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
    if x_correlation_id != payload.correlation_id or x_correlation_id != context["correlation_id"]:
        raise AgentError("AI_INVALID_REQUEST", "Correlation id mismatch", 400)
    context["_raw_token"] = x_ai_context
    context["conversation_id"] = payload.conversation_id
    started = time.monotonic()
    results = []
    history = get_history(payload.conversation_id, context)
    allow_writes = bool(CONFIRMATION.search(payload.message))
    current = await provider.complete(payload.message, context, results, history, allow_writes)
    for _ in range(settings.max_tool_calls):
        if not current.tool_call:
            break
        results.append(await execute_tool(current.tool_call, context, payload.message))
        current = await provider.complete(payload.message, context, results, history, allow_writes)
        if time.monotonic() - started > 25:
            raise AgentError("AI_DEADLINE_EXCEEDED", "Global deadline exceeded", 504, True)
    if not current.content:
        raise AgentError("AI_RESPONSE_INVALID", "LLM returned no content", 502)
    save_turn(payload.conversation_id, context, payload.message, current.content)
    last_action = results[-1].get("data") if results and isinstance(results[-1], dict) else None
    ui = {"type": "message", "schema": None, "data": None, "actions": []}
    operation = None
    if isinstance(last_action, dict) and last_action.get("status") == "confirmation_required":
        proposal = last_action.get("proposal", {})
        if last_action.get("tool") == "createFair":
            fields = [
                {"name": "name", "type": "text", "label": "Nombre", "required": True, "max_length": 200},
                {"name": "description", "type": "textarea", "label": "Descripción", "required": False, "max_length": 5000},
                {"name": "starts_at", "type": "date", "label": "Inicio", "required": False},
                {"name": "ends_at", "type": "date", "label": "Fin", "required": False},
                {"name": "registration_deadline", "type": "date", "label": "Cierre de inscripción", "required": False},
                {"name": "image_url", "type": "image", "label": "Portada", "required": True},
            ]
            title = "Crear feria"
        else:
            title_by_tool = {
                "createFairRubric": "Crear rúbrica",
                "addFairRubricCriterion": "Agregar criterio de rúbrica",
                "createCategoryFairRubric": "Crear rúbrica por categoría",
                "addCategoryFairRubricCriterion": "Agregar criterio por categoría",
                "createTeachingCriterion": "Crear criterio de evaluación docente",
                "updateTeachingCriterion": "Actualizar criterio de evaluación docente",
                "toggleTeachingCriterion": "Cambiar estado del criterio docente",
                "deleteTeachingCriterion": "Eliminar criterio de evaluación docente",
                "updateFair": "Actualizar feria",
                "changeFairStatus": "Cambiar estado de feria",
                "createFairStand": "Crear stand",
                "updateFairStand": "Actualizar stand",
                "deleteFairStand": "Eliminar stand",
            }
            labels = {
                "name": "Nombre", "description": "Descripción", "code": "Código del stand",
                "min_score": "Puntaje mínimo", "max_score": "Puntaje máximo", "position": "Posición",
                "points": "Puntos",
                "status": "Estado", "starts_at": "Inicio", "ends_at": "Fin",
            }
            fields = [
                {"name": key, "type": "number" if key in {"min_score", "max_score", "position"} else "date" if key in {"starts_at", "ends_at"} else "select" if key == "status" else "textarea" if key == "description" else "text",
                 "label": labels.get(key, key.replace("_", " ").capitalize()), "required": False,
                 **({"options": [{"value": status, "label": status} for status in ["DRAFT", "OPEN", "CLOSED"]]} if key == "status" else {}),
                 **({"max_length": 5000} if key == "description" else {})}
                for key in proposal if not key.endswith("_id")
            ]
            title = title_by_tool.get(last_action.get("tool"), "Revisar operación")
        ui = {
            "type": "confirmation",
            "schema": {"title": title, "fields": fields},
            "data": {**{key: value for key, value in proposal.items() if not key.endswith("_id")}, "site_name": last_action.get("site_name")},
            "actions": ["confirm", "edit", "cancel"],
        }
        operation = {
            "id": last_action.get("operation_id"),
            "version": last_action.get("version"),
            "status": "awaiting_confirmation",
        }
    elif isinstance(last_action, dict) and last_action.get("status") == "created":
        ui = {
            "type": "success",
            "schema": None,
            "data": last_action.get("fair"),
            "actions": [],
        }
        operation = {
            "id": last_action.get("operation_id"),
            "version": last_action.get("version"),
            "status": "completed",
        }
    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "message": {"role": "assistant", "content": (
            "Revisa los datos de la feria en la tarjeta. Sube la portada desde aquí; podrás confirmar cuando esté lista."
            if isinstance(last_action, dict) and last_action.get("status") == "confirmation_required" and last_action.get("tool") == "createFair"
            else current.content[:4000]
        )},
        "ui": ui,
        "operation": operation,
        "citations": [],
        "suggested_actions": [],
        "meta": {"correlation_id": payload.correlation_id, "tool_calls": len(results), "truncated": False},
    }
