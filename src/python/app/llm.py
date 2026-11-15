from __future__ import annotations

import json
import logging
import time
import uuid

import httpx

from .config import settings
from .errors import AgentError
from .models import LLMResult, ToolCall
from .observability import request_id_context

TOOLS = [
    "getSiteOverview",
    "getSiteFairs",
    "getSiteProjects",
    "getSiteStands",
    "getSiteResults",
    "listTeachingCriteria",
    "createTeachingCriterion",
    "updateTeachingCriterion",
    "toggleTeachingCriterion",
    "deleteTeachingCriterion",
    "listFairCategories",
    "listFairCategoryRubrics",
    "createFairRubric",
    "addFairRubricCriterion",
    "createCategoryFairRubric",
    "addCategoryFairRubricCriterion",
    "createFair",
    "updateFair",
    "changeFairStatus",
    "listFairStands",
    "createFairStand",
    "updateFairStand",
    "deleteFairStand",
]
logger = logging.getLogger(__name__)
READ_TOOLS = {
    "getSiteOverview",
    "getSiteFairs",
    "getSiteProjects",
    "getSiteStands",
    "getSiteResults",
    "listFairStands",
    "listFairCategories",
    "listFairCategoryRubrics",
    "listTeachingCriteria",
}
WRITE_TOOLS = set(TOOLS) - READ_TOOLS


class LLMProvider:
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        raise NotImplementedError


def site_instructions(context: dict) -> str:
    parts = [
        "La sede activa ya fue resuelta y autorizada por CampusVote.",
        "No solicites site_id, organization_id, UUIDs ni ningún identificador interno.",
        "No pidas al usuario que elija o confirme una sede por su ID.",
        "Las herramientas operan siempre sobre la única sede activa del contexto firmado.",
    ]
    site_name = context.get("site_name")
    if isinstance(site_name, str) and site_name.strip():
        parts.append(f"Sede activa: {site_name.strip()}.")
    return " ".join(parts)


def system_prompt(context: dict) -> str:
    return (
        "Eres CampusVote, un asistente conversacional de la sede activa. Habla en el idioma "
        "del usuario, con un tono humano, cordial y claro. Reconoce lo que ya se dijo y haz "
        "una sola pregunta concreta cuando falte información.\n"
        "REGLAS OBLIGATORIAS:\n"
        "1. Nunca inventes datos, permisos, resultados ni identificadores. Para responder con "
        "datos de la sede usa las herramientas disponibles.\n"
        "2. Para consultas agregadas (resumen, ferias, proyectos, stands, resultados, rúbricas "
        "o criterios) llama primero la herramienta de lectura correspondiente.\n"
        "3. Cuando el usuario pida crear, actualizar o eliminar algo, llama la herramienta de "
        "escritura con los datos disponibles. El servidor no ejecutará el cambio: devolverá una "
        "propuesta con status confirmation_required. NUNCA digas que ya se creó o modificó.\n"
        "4. Si una herramienta devuelve confirmation_required, no la vuelvas a llamar: presenta "
        "un resumen claro de la propuesta y pide confirmación explícita al usuario.\n"
        "5. Si faltan datos obligatorios, pide solo los que falten antes de proponer.\n"
        "6. Para crear una feria no pidas ni inventes URLs de portada: la tarjeta de la operación "
        "permite subir el archivo a CampusVote. Recopila fechas opcionales y muestra todos los datos.\n"
        "7. Para el dominio académico lista primero los criterios; al crear solo solicita nombre y "
        "descripción. Para modificar criterios usa los registros del listado autorizado.\n"
        "8. Las fechas deben enviarse en formato ISO 8601 (por ejemplo 2026-05-01T09:00:00Z).\n"
        "9. No menciones detalles técnicos internos (nombres de herramientas, tokens, IDs).\n"
        "10. Si no puedes completar una operación, explica el motivo en lenguaje natural.\n"
        f"{site_instructions(context)}"
    )


class StaticProvider(LLMProvider):
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        return LLMResult(
            content="Puedo conversar contigo, consultar y listar información agregada de tus sedes asignadas."
        )


FALLBACK_ERROR_CODES = frozenset({
    "LLM_AUTH_FAILED",
    "LLM_MODEL_NOT_FOUND",
    "LLM_RATE_LIMITED",
    "LLM_PROVIDER_TIMEOUT",
    "LLM_PROVIDER_UNAVAILABLE",
})


def fallback_endpoint() -> tuple[str, str, str] | None:
    """Return the configured secondary provider, if all three values are set."""
    url = (getattr(settings, "llm_fallback_url", "") or "").strip()
    model = (getattr(settings, "llm_fallback_model", "") or "").strip()
    api_key = (getattr(settings, "llm_fallback_api_key", "") or "").strip()
    if url and model and api_key:
        return url, model, api_key
    return None


class OpenAICompatibleProvider(LLMProvider):
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        if not settings.llm_provider or not settings.llm_url or not settings.llm_model or not settings.llm_api_key:
            raise AgentError("LLM_CONFIG_MISSING", "LLM provider configuration is incomplete", 503)
        payload: dict = {
            "model": settings.llm_model,
            "messages": [{"role": "system", "content": system_prompt(context)}, *messages],
        }
        if allow_tools:
            payload["tools"] = [
                {"type": "function", "function": tool_schema(name)} for name in TOOLS
            ]
            payload["tool_choice"] = "auto"
        # Total budget shared by the primary attempt and the fallback, so a slow
        # primary cannot swallow the whole turn before the secondary is tried.
        budget_ends = time.monotonic() + settings.llm_total_budget_seconds
        try:
            return await self._send(
                settings.llm_url, settings.llm_model, settings.llm_api_key, payload,
                timeout=min(settings.llm_timeout_seconds, settings.llm_total_budget_seconds),
            )
        except AgentError as error:
            fallback = fallback_endpoint()
            if fallback is None or error.code not in FALLBACK_ERROR_CODES:
                raise
            remaining = budget_ends - time.monotonic()
            if remaining < 1:
                raise
            url, model, api_key = fallback
            logger.warning(
                "llm_fallback_engaged requestId=%s primary_model=%s fallback_model=%s cause=%s",
                request_id_context.get(), settings.llm_model, model, error.code,
            )
            return await self._send(
                url, model, api_key, {**payload, "model": model},
                timeout=min(settings.llm_timeout_seconds, remaining),
            )

    async def _send(self, url: str, model: str, api_key: str, payload: dict, timeout: float | None = None) -> LLMResult:
        try:
            async with httpx.AsyncClient(timeout=timeout or settings.llm_timeout_seconds) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": "Bearer " + api_key},
                )
                if response.status_code >= 400:
                    status = response.status_code
                    if status in (401, 403):
                        code, public_status = "LLM_AUTH_FAILED", 502
                    elif status == 404:
                        code, public_status = "LLM_MODEL_NOT_FOUND", 502
                    elif status == 429:
                        code, public_status = "LLM_RATE_LIMITED", 429
                    else:
                        code, public_status = "LLM_PROVIDER_UNAVAILABLE", 503
                    logger.warning(
                        "LLM request failed requestId=%s provider=%s model=%s status=%s cause=http_status",
                        request_id_context.get(), settings.llm_provider, model, status,
                    )
                    raise AgentError(code, "LLM provider request failed", public_status, status == 429 or status >= 500)
                body = response.json()
                choice = body["choices"][0]["message"]
                if not isinstance(choice, dict):
                    raise ValueError("message must be an object")
        except httpx.TimeoutException as error:
            logger.warning("LLM request failed requestId=%s provider=%s model=%s cause=timeout", request_id_context.get(), settings.llm_provider, model)
            raise AgentError("LLM_PROVIDER_TIMEOUT", "LLM provider timed out", 504, True) from error
        except AgentError:
            raise
        except httpx.RequestError as error:
            logger.warning(
                "LLM request failed requestId=%s provider=%s model=%s cause=%s",
                request_id_context.get(), settings.llm_provider, model, type(error).__name__,
            )
            raise AgentError("LLM_PROVIDER_UNAVAILABLE", "LLM provider is unavailable", 503, True) from error
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning(
                "LLM response invalid requestId=%s provider=%s model=%s cause=%s",
                request_id_context.get(), settings.llm_provider, model, type(error).__name__,
            )
            raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502) from error

        content = choice.get("content")
        if not isinstance(content, str):
            content = ""
        raw_calls = choice.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502)
        tool_calls: list[ToolCall] = []
        if payload.get("tools"):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    raise AgentError("LLM_INVALID_RESPONSE", "LLM provider returned an invalid response", 502)
                function = raw.get("function") or {}
                name = function.get("name")
                if not isinstance(name, str) or name not in TOOLS:
                    logger.warning(
                        "LLM requested unknown tool requestId=%s tool=%s",
                        request_id_context.get(), name,
                    )
                    raise AgentError("AI_TOOL_NOT_FOUND", "LLM requested an unknown tool", 502)
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError as error:
                        raise AgentError("AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502) from error
                if arguments is None:
                    arguments = {}
                if not isinstance(arguments, dict):
                    raise AgentError("AI_RESPONSE_INVALID", "LLM returned invalid tool arguments", 502)
                tool_calls.append(
                    ToolCall(
                        id=str(raw.get("id") or f"call_{uuid.uuid4().hex}"),
                        name=name,
                        arguments=arguments,
                    )
                )
        if tool_calls:
            return LLMResult(content=content or None, tool_calls=tool_calls)
        if not content.strip():
            raise AgentError("AI_RESPONSE_INVALID", "LLM returned no content", 502)
        return LLMResult(content=content)


def tool_schema(name: str) -> dict:
    if name == "createTeachingCriterion":
        properties = {"name": {"type": "string"}, "description": {"type": "string"}}
        required = ["name"]
        description = "Propone un criterio general de evaluación docente para la sede activa."
    elif name == "updateTeachingCriterion":
        properties = {"criterion_id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}}
        required = ["criterion_id"]
        description = "Propone actualizar un criterio existente usando su ID del listado autorizado."
    elif name in {"toggleTeachingCriterion", "deleteTeachingCriterion"}:
        properties = {"criterion_id": {"type": "string"}}
        required = ["criterion_id"]
        description = "Propone cambiar el estado o eliminar un criterio usando su ID del listado autorizado."
    elif name == "createFairRubric":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "name"]
        description = "Propone una rúbrica para una feria en borrador."
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
        description = "Propone agregar un criterio a una rúbrica de feria."
    elif name == "createCategoryFairRubric":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "category_id", "name"]
        description = "Propone una rúbrica para una categoría de feria en borrador."
    elif name == "addCategoryFairRubricCriterion":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "points": {"type": "number"},
            "position": {"type": "integer"},
        }
        required = ["fair_id", "category_id", "name", "points"]
        description = "Propone agregar un criterio a una rúbrica de categoría; la suma activa se limita a 20 puntos."
    elif name == "createFair":
        properties = {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "image_url": {"type": "string"},
            "starts_at": {"type": "string"},
            "ends_at": {"type": "string"},
            "registration_deadline": {"type": "string"},
        }
        required = ["name"]
        description = "Propone una feria. No inventes image_url; la tarjeta permite subir la portada autenticadamente."
    elif name == "updateFair":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "starts_at": {"type": "string"},
            "ends_at": {"type": "string"},
        }
        required = ["fair_id"]
        description = "Propone actualizar una feria autorizada."
    elif name == "changeFairStatus":
        properties = {
            "fair_id": {"type": "string"},
            "status": {"type": "string", "enum": ["DRAFT", "OPEN", "CLOSED"]},
        }
        required = ["fair_id", "status"]
        description = "Propone cambiar el estado de una feria."
    elif name == "listFairStands":
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Lista los stands de una feria autorizada."
    elif name in {"listFairCategories", "listFairCategoryRubrics"}:
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Consulta las categorías o rúbricas por categoría de una feria autorizada."
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
        description = "Propone gestionar un stand de una feria."
    elif name == "deleteFairStand":
        properties = {
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
        }
        required = ["fair_id", "stand_id"]
        description = "Propone eliminar un stand."
    elif name == "listTeachingCriteria":
        properties = {}
        required = []
        description = "Lista los criterios de evaluación docente configurados para la sede activa."
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
        },
    }


def get_provider() -> LLMProvider:
    return StaticProvider() if settings.llm_provider == "static" else OpenAICompatibleProvider()
