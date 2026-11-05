from __future__ import annotations

import json
import logging
import re

import httpx

from .config import settings
from .errors import AgentError
from .models import LLMResult

TOOLS = [
    "getSiteOverview",
    "getSiteFairs",
    "getSiteProjects",
    "getSiteStands",
    "getSiteResults",
    "createFairRubric",
    "addFairRubricCriterion",
    "createFair",
    "updateFair",
    "changeFairStatus",
    "listFairStands",
    "createFairStand",
    "updateFairStand",
    "deleteFairStand",
]
logger = logging.getLogger(__name__)
READ_TOOLS = set(TOOLS[:5] + ["listFairStands"])
WRITE_TOOLS = set(TOOLS) - READ_TOOLS
CONFIRMATION = re.compile(
    r"\b(confirmo|confirmar|confirma|sí,\s*crear|si,\s*crear|adelante)\b",
    re.IGNORECASE,
)


class LLMProvider:
    async def complete(self, message, context, tool_results, history=None, allow_writes=False) -> LLMResult:
        raise NotImplementedError


def site_instructions(context: dict) -> str:
    parts = [
        "La sede activa ya fue resuelta y autorizada por CampusVote.",
        "No solicites site_id, organization_id, UUIDs ni ning?n identificador interno.",
        "No pidas al usuario que elija o confirme una sede por su ID.",
        "Las herramientas operan siempre sobre la ?nica sede activa del contexto firmado.",
    ]
    site_name = context.get("site_name")
    if isinstance(site_name, str) and site_name.strip():
        parts.append(f"Sede activa: {site_name.strip()}.")
    return " ".join(parts)


class StaticProvider(LLMProvider):
    async def complete(self, message, context, tool_results, history=None, allow_writes=False) -> LLMResult:
        if tool_results:
            return LLMResult(content="Consulta completada con datos autorizados de la sede.")
        return LLMResult(content="Puedo conversar contigo, consultar y listar información agregada de tus sedes asignadas.")


class OpenAICompatibleProvider(LLMProvider):
    async def complete(self, message, context, tool_results, history=None, allow_writes=False) -> LLMResult:
        if not settings.llm_provider or not settings.llm_url or not settings.llm_model or not settings.llm_api_key:
            raise AgentError("LLM_CONFIG_MISSING", "LLM provider configuration is incomplete", 503)
        messages = [{
            "role": "system",
            "content": (
                "Eres CampusVote, un asistente conversacional humanoide, cordial y claro. "
                "Responde en el idioma del usuario. Reconoce lo que ya se dijo y haz una sola "
                "pregunta concreta cuando falte información. Consulta y lista solo datos agregados "
                "de sedes autorizadas. Nunca inventes datos, permisos ni resultados. Para crear una "
                "rúbrica, recopila los datos, resume la propuesta y pide confirmación explícita. "
                "Solo después de que el usuario diga que confirma puedes llamar herramientas de escritura. "
                "Consultas y listados no requieren confirmación. "
                f"{site_instructions(context)}"
            ),
        }]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "tools": [
                {"type": "function", "function": tool_schema(name)}
                for name in TOOLS
                if name in READ_TOOLS or (allow_writes and name in WRITE_TOOLS)
            ],
        }
        if tool_results:
            payload["messages"].append({"role": "tool", "content": json.dumps(tool_results, ensure_ascii=False)})
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    settings.llm_url,
                    json=payload,
                    headers={"Authorization": "Bearer " + settings.llm_api_key},
                )
                if response.status_code >= 400:
                    status = response.status_code
                    if status in (401, 403):
                        code, public_status = "LLM_AUTH_FAILED", 502
                    elif status == 404:
                        code, public_status = "LLM_MODEL_NOT_FOUND", 502
                    elif status == 429:
                        code, public_status = "LLM_RATE_LIMITED", 503
                    else:
                        code, public_status = "LLM_PROVIDER_UNAVAILABLE", 503
                    logger.warning(
                        "LLM request failed provider=%s model=%s status=%s cause=http_status",
                        settings.llm_provider, settings.llm_model, status,
                    )
                    raise AgentError(code, "LLM provider request failed", public_status, status == 429 or status >= 500)
                body = response.json()
                choice = body["choices"][0]["message"]
                if not isinstance(choice, dict):
                    raise ValueError("message must be an object")
        except httpx.TimeoutException as error:
            logger.warning("LLM request failed provider=%s model=%s cause=timeout", settings.llm_provider, settings.llm_model)
            raise AgentError("LLM_PROVIDER_TIMEOUT", "LLM provider timed out", 504, True) from error
        except AgentError:
            raise
        except httpx.RequestError as error:
            logger.warning(
                "LLM request failed provider=%s model=%s cause=%s",
                settings.llm_provider, settings.llm_model, type(error).__name__,
            )
            raise AgentError("LLM_PROVIDER_UNAVAILABLE", "LLM provider is unavailable", 503, True) from error
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning(
                "LLM response invalid provider=%s model=%s cause=%s",
                settings.llm_provider, settings.llm_model, type(error).__name__,
            )
            raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502) from error
        tool_calls = choice.get("tool_calls") or []
        if tool_calls:
            function = tool_calls[0]["function"]
            try:
                arguments = json.loads(function["arguments"])
            except (TypeError, json.JSONDecodeError) as error:
                raise AgentError("AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502) from error
            return LLMResult(tool_call={"name": function["name"], "arguments": arguments})
        return LLMResult(content=choice.get("content") or "")


def tool_schema(name: str) -> dict:
    if name == "createFairRubric":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "name"]
        description = "Crea una rúbrica confirmada para una feria DRAFT."
    elif name == "addFairRubricCriterion":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "min_score": {"type": "number"},
            "max_score": {"type": "number"},
            "position": {"type": "integer"},
        }
        required = ["fair_id", "name", "min_score", "max_score"]
        description = "Agrega un criterio confirmado a una rúbrica."
    elif name == "createFair":
        properties = {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "starts_at": {"type": "string"},
            "ends_at": {"type": "string"},
        }
        required = ["name"]
        description = "Crea una feria en una sede autorizada, después de confirmación."
    elif name == "updateFair":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "starts_at": {"type": "string"},
            "ends_at": {"type": "string"},
        }
        required = ["fair_id"]
        description = "Actualiza una feria autorizada, después de confirmación."
    elif name == "changeFairStatus":
        properties = {
            "fair_id": {"type": "string"},
            "status": {"type": "string", "enum": ["DRAFT", "OPEN", "CLOSED"]},
        }
        required = ["fair_id", "status"]
        description = "Cambia el estado de una feria; requiere confirmación reforzada."
    elif name == "listFairStands":
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Lista los stands de una feria autorizada."
    elif name in {"createFairStand", "updateFairStand"}:
        properties = {
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
            "code": {"type": "string"},
            "description": {"type": "string"},
        }
        required = (
            ["fair_id", "code"]
            if name == "createFairStand"
            else ["fair_id", "stand_id"]
        )
        description = "Gestiona un stand de una feria; requiere confirmación."
    elif name == "deleteFairStand":
        properties = {
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
        }
        required = ["fair_id", "stand_id"]
        description = "Elimina un stand; requiere confirmación reforzada."
    else:
        properties = {}
        required = []
        description = f"Consulta y lista datos agregados de la sede activa usando {name}."
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def get_provider() -> LLMProvider:
    return StaticProvider() if settings.llm_provider == "static" else OpenAICompatibleProvider()
