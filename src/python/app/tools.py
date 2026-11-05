import uuid
import re
import httpx

from .config import settings
from .errors import AgentError
from .models import ToolCall

WRITE_TOOLS = {
    "createFairRubric",
    "addFairRubricCriterion",
    "createFair",
    "updateFair",
    "changeFairStatus",
    "createFairStand",
    "updateFairStand",
    "deleteFairStand",
}
SENSITIVE_TOOLS = {"changeFairStatus", "deleteFairStand"}
CONFIRMATION = re.compile(
    r"\b(confirmo|confirmar|confirma|sí,\s*crear|si,\s*crear|adelante)\b",
    re.IGNORECASE,
)

async def execute_tool(tool_call: ToolCall, context: dict, user_message: str = "") -> dict:
    if tool_call.name not in {
        "getSiteOverview",
        "getSiteFairs",
        "getSiteProjects",
        "getSiteStands",
        "getSiteResults",
        *WRITE_TOOLS,
        "listFairStands",
    }:
        raise AgentError("AI_TOOL_NOT_FOUND", "Tool is not allowlisted", 404)
    site_id = context.get("site_id")
    if not isinstance(site_id, str) or not site_id:
        raise AgentError("AI_SITE_ASSIGNMENT_REQUIRED", "The administrator has no active site assignment", 403)
    if tool_call.name in WRITE_TOOLS and not CONFIRMATION.search(user_message):
        raise AgentError("AI_CONFIRMATION_REQUIRED", "Explicit confirmation is required before creating records", 409)
    if tool_call.name in SENSITIVE_TOOLS and not re.search(
        r"\b(confirmo dos veces|segunda confirmación|confirmo definitivamente)\b",
        user_message,
        re.IGNORECASE,
    ):
        raise AgentError(
            "AI_DOUBLE_CONFIRMATION_REQUIRED",
            "A second explicit confirmation is required for this sensitive action",
            409,
        )
    scope_arguments = {
        "site_id",
        "siteId",
        "organization_id",
        "organizationId",
        "role",
        "scope_level",
        "scopeLevel",
        "user_id",
        "userId",
        "confirmed",
    }
    input_payload = {
        key: value
        for key, value in tool_call.arguments.items()
        if key not in scope_arguments
    }
    input_payload["site_id"] = site_id
    body = {
        "contract_version": "1.0",
        "correlation_id": context["correlation_id"],
        "tool_call_id": str(uuid.uuid4()),
        "input": input_payload,
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
