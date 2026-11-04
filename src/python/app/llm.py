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
]


class LLMProvider:
    async def complete(self, message: str, context: dict, tool_results: list[dict]) -> LLMResult:
        raise NotImplementedError


class StaticProvider(LLMProvider):
    async def complete(self, message: str, context: dict, tool_results: list[dict]) -> LLMResult:
        if tool_results:
            return LLMResult(content="Consulta completada con datos autorizados de la sede.")
        return LLMResult(content="Puedo consultar información agregada de tus sedes asignadas.")


class OpenAICompatibleProvider(LLMProvider):
    async def complete(self, message: str, context: dict, tool_results: list[dict]) -> LLMResult:
        if not settings.llm_url or not settings.llm_model or not settings.llm_api_key:
            raise AgentError("AI_LLM_UNAVAILABLE", "LLM provider is not configured", 503, True)
        payload = {
            "model": settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a read-only assistant for an ADMIN SITE. Never invent permissions or data.",
                },
                {"role": "user", "content": message},
            ],
            "tools": [{"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {"site_id": {"type": "string"}}, "required": ["site_id"], "additionalProperties": False}}} for name in TOOLS],
        }
        if tool_results:
            payload["messages"].append({"role": "tool", "content": str(tool_results)})
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(settings.llm_url, json=payload, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
                response.raise_for_status()
                choice = response.json()["choices"][0]["message"]
        except httpx.TimeoutException as error:
            raise AgentError("AI_TOOL_TIMEOUT", "LLM timed out", 504, True) from error
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as error:
            raise AgentError("AI_LLM_UNAVAILABLE", "LLM provider unavailable", 503, True) from error
        tool_calls = choice.get("tool_calls") or []
        if tool_calls:
            function = tool_calls[0]["function"]
            import json
            return LLMResult(tool_call={"name": function["name"], "arguments": json.loads(function["arguments"])})
        return LLMResult(content=choice.get("content") or "")


def get_provider() -> LLMProvider:
    return StaticProvider() if settings.llm_provider == "static" else OpenAICompatibleProvider()
