import uuid

import httpx

from .config import settings
from .errors import AgentError, CORRECTABLE_CODES
from .models import ToolCall

WRITE_TOOLS = {
    "createFairRubric",
    "addFairRubricCriterion",
    "createCategoryFairRubric",
    "addCategoryFairRubricCriterion",
    "createFairCategory",
    "updateFairCategory",
    "deleteFairCategory",
    "createFair",
    "updateFair",
    "changeFairStatus",
    "createFairStand",
    "updateFairStand",
    "deleteFairStand",
    "createTeachingCriterion",
    "updateTeachingCriterion",
    "toggleTeachingCriterion",
    "deleteTeachingCriterion",
}


async def execute_tool(
    tool_call: ToolCall,
    context: dict,
    user_message: str = "",
    remaining_seconds: float | None = None,
) -> dict:
    """Ejecuta la herramienta acotando la espera al presupuesto global restante.

    Limitación: el timeout acota la espera HTTP de Python hacia Node. El trabajo
    que Node realice después de recibir la petición (incluida la confirmación de
    escrituras) queda fuera del control de este proceso; no se afirma garantía
    sobre esa etapa.
    """
    if remaining_seconds is not None and remaining_seconds <= 0:
        raise AgentError("AI_DEADLINE_EXCEEDED", "Request deadline exceeded", 504, True)
    if tool_call.name not in {
        "getSiteOverview",
        "getSiteFairs",
        "getSiteProjects",
        "getSiteStands",
        "getSiteResults",
        *WRITE_TOOLS,
        "listFairStands",
        "listFairCategories",
        "listFairCategoryRubrics",
        "getFairRubric",
        "setup_category_with_rubric",
        "listTeachingCriteria",
    }:
        raise AgentError("AI_TOOL_NOT_FOUND", "Tool is not allowlisted", 404)
    site_id = context.get("site_id")
    if not isinstance(site_id, str) or not site_id:
        raise AgentError("AI_SITE_ASSIGNMENT_REQUIRED", "The administrator has no active site assignment", 403)
    # Python never writes directly: write tools are sent to Node, which turns
    # them into a pending proposal. The real execution happens only after the
    # administrator confirms in the Node orchestration layer.
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
        "user_message": user_message,
        "conversation_id": context.get("conversation_id"),
    }
    try:
        timeout = settings.tool_timeout_seconds
        if remaining_seconds is not None:
            timeout = min(timeout, remaining_seconds)
        client_kwargs = {"timeout": timeout}
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
        code = details.get("code", "AI_TOOL_UNAVAILABLE")
        message = details.get("message", "Tool failed")
        # Los 4xx de validación/negocio traen un mensaje seguro y accionable
        # (qué campo falló). Se propaga para que el usuario y el modelo sepan
        # exactamente qué corregir en lugar de recibir un mensaje genérico.
        expose = 400 <= response.status_code < 500 and code in CORRECTABLE_CODES
        raise AgentError(
            code,
            message,
            response.status_code,
            details.get("retryable", False),
            public_message=message if expose and isinstance(message, str) else None,
            details=details.get("details") if isinstance(details.get("details"), (dict, list)) else None,
        )
    return response.json()
