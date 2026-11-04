from __future__ import annotations

import json
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
READ_TOOLS = set(TOOLS[:5] + ["listFairStands"])
WRITE_TOOLS = set(TOOLS) - READ_TOOLS
CONFIRMATION = re.compile(
    r"\b(confirmo|confirmar|confirma|sí,\s*crear|si,\s*crear|adelante)\b",
    re.IGNORECASE,
)


class LLMProvider:
    async def complete(self, message, context, tool_results, history=None, allow_writes=False) -> LLMResult:
        raise NotImplementedError


class StaticProvider(LLMProvider):
    async def complete(self, message, context, tool_results, history=None, allow_writes=False) -> LLMResult:
        if tool_results:
            return LLMResult(content="Consulta completada con datos autorizados de la sede.")
        return LLMResult(content="Puedo conversar contigo, consultar y listar información agregada de tus sedes asignadas.")


class OpenAICompatibleProvider(LLMProvider):
    async def complete(self, message, context, tool_results, history=None, allow_writes=False) -> LLMResult:
        if not settings.llm_url or not settings.llm_model or not settings.llm_api_key:
            raise AgentError("AI_LLM_UNAVAILABLE", "LLM provider is not configured", 503, True)
        messages = [{
            "role": "system",
            "content": (
                "Eres CampusVote, un asistente conversacional humanoide, cordial y claro. "
                "Responde en el idioma del usuario. Reconoce lo que ya se dijo y haz una sola "
                "pregunta concreta cuando falte información. Consulta y lista solo datos agregados "
                "de sedes autorizadas. Nunca inventes datos, permisos ni resultados. Para crear una "
                "rúbrica, recopila los datos, resume la propuesta y pide confirmación explícita. "
                "Solo después de que el usuario diga que confirma puedes llamar herramientas de escritura. "
                "Consultas y listados no requieren confirmación."
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
                response.raise_for_status()
                choice = response.json()["choices"][0]["message"]
        except httpx.TimeoutException as error:
            raise AgentError("AI_TOOL_TIMEOUT", "LLM timed out", 504, True) from error
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise AgentError("AI_LLM_UNAVAILABLE", "LLM provider unavailable", 503, True) from error
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
            "site_id": {"type": "string"},
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["site_id", "fair_id", "name"]
        description = "Crea una rúbrica confirmada para una feria DRAFT."
    elif name == "addFairRubricCriterion":
        properties = {
            "site_id": {"type": "string"},
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "min_score": {"type": "number"},
            "max_score": {"type": "number"},
            "position": {"type": "integer"},
        }
        required = ["site_id", "fair_id", "name", "min_score", "max_score"]
        description = "Agrega un criterio confirmado a una rúbrica."
    elif name == "createFair":
        properties = {
            "site_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "starts_at": {"type": "string"},
            "ends_at": {"type": "string"},
        }
        required = ["site_id", "name"]
        description = "Crea una feria en una sede autorizada, después de confirmación."
    elif name == "updateFair":
        properties = {
            "site_id": {"type": "string"},
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "starts_at": {"type": "string"},
            "ends_at": {"type": "string"},
        }
        required = ["site_id", "fair_id"]
        description = "Actualiza una feria autorizada, después de confirmación."
    elif name == "changeFairStatus":
        properties = {
            "site_id": {"type": "string"},
            "fair_id": {"type": "string"},
            "status": {"type": "string", "enum": ["DRAFT", "OPEN", "CLOSED"]},
        }
        required = ["site_id", "fair_id", "status"]
        description = "Cambia el estado de una feria; requiere confirmación reforzada."
    elif name == "listFairStands":
        properties = {"site_id": {"type": "string"}, "fair_id": {"type": "string"}}
        required = ["site_id", "fair_id"]
        description = "Lista los stands de una feria autorizada."
    elif name in {"createFairStand", "updateFairStand"}:
        properties = {
            "site_id": {"type": "string"},
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
            "code": {"type": "string"},
            "description": {"type": "string"},
        }
        required = (
            ["site_id", "fair_id", "code"]
            if name == "createFairStand"
            else ["site_id", "fair_id", "stand_id"]
        )
        description = "Gestiona un stand de una feria; requiere confirmación."
    elif name == "deleteFairStand":
        properties = {
            "site_id": {"type": "string"},
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
        }
        required = ["site_id", "fair_id", "stand_id"]
        description = "Elimina un stand; requiere confirmación reforzada."
    else:
        properties = {"site_id": {"type": "string"}}
        required = ["site_id"]
        description = f"Consulta y lista datos agregados usando {name}."
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
