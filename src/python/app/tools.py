import uuid
import httpx

from .config import settings
from .errors import AgentError
from .models import ToolCall


async def execute_tool(tool_call: ToolCall, context: dict) -> dict:
    if tool_call.name not in {
        "getSiteOverview",
        "getSiteFairs",
        "getSiteProjects",
        "getSiteStands",
        "getSiteResults",
    }:
        raise AgentError("AI_TOOL_NOT_FOUND", "Tool is not allowlisted", 404)
    site_id = tool_call.arguments.get("site_id")
    if not isinstance(site_id, str) or site_id not in context["site_ids"]:
        raise AgentError("AI_SCOPE_FORBIDDEN", "Site is not authorized", 403)
    body = {
        "contract_version": "1.0",
        "correlation_id": context["correlation_id"],
        "tool_call_id": str(uuid.uuid4()),
        "input": {"site_id": site_id},
    }
    try:
        client_kwargs = {"timeout": settings.tool_timeout_seconds}
        if settings.mtls_required:
            client_kwargs["cert"] = (settings.mtls_cert_path, settings.mtls_key_path)
            client_kwargs["verify"] = settings.mtls_ca_path
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(
                f"{settings.node_tools_url.rstrip('/')}/internal/ai/v1/tools/{tool_call.name}",
                json=body,
                headers={
                    "Authorization": f"Bearer {settings.service_token}",
                    "X-AI-Context": context["_raw_token"],
                    "X-Correlation-Id": context["correlation_id"],
                },
            )
    except httpx.TimeoutException as error:
        raise AgentError("AI_TOOL_TIMEOUT", "Tool timed out", 504, True) from error
    except httpx.HTTPError as error:
        raise AgentError("AI_TOOL_UNAVAILABLE", "Tool service unavailable", 503, True) from error
    if response.status_code >= 400:
        payload = response.json()
        details = payload.get("error", {})
        raise AgentError(details.get("code", "AI_TOOL_UNAVAILABLE"), details.get("message", "Tool failed"), response.status_code, details.get("retryable", False))
    return response.json()
