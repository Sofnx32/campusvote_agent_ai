from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .config import settings
from .context import verify_context, verify_service_token
from .errors import AgentError, error_payload
from .llm import get_provider
from .models import ChatRequest
from .tools import execute_tool

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("campusvote-agent-python")
provider = get_provider()


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
    started = time.monotonic()
    results = []
    current = await provider.complete(payload.message, context, results)
    for _ in range(settings.max_tool_calls):
        if not current.tool_call:
            break
        results.append(await execute_tool(current.tool_call, context))
        current = await provider.complete(payload.message, context, results)
        if time.monotonic() - started > 25:
            raise AgentError("AI_DEADLINE_EXCEEDED", "Global deadline exceeded", 504, True)
    if not current.content:
        raise AgentError("AI_RESPONSE_INVALID", "LLM returned no content", 502)
    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "message": {"role": "assistant", "content": current.content[:4000]},
        "citations": [],
        "suggested_actions": [],
        "meta": {"correlation_id": payload.correlation_id, "tool_calls": len(results), "truncated": False},
    }
