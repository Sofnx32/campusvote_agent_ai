from __future__ import annotations


from datetime import datetime, timezone
import jwt

from .config import settings
from .errors import AgentError


def verify_service_token(value: str | None) -> None:
    if not value or value != f"Bearer {settings.service_token}":
        raise AgentError("AI_SERVICE_UNAUTHORIZED", "Invalid service token", 401)
    if settings.environment == "production" and len(settings.service_token) < 32:
        raise AgentError("AI_SERVICE_UNAUTHORIZED", "Service token is too short", 401)


def verify_context(value: str | None) -> dict:
    if not value:
        raise AgentError("AI_CONTEXT_INVALID", "AI context is required", 401)
    key = settings.public_key()
    if not key:
        raise AgentError("AI_CONTEXT_INVALID", "Context verification key is not configured", 401)
    try:
        claims = jwt.decode(
            value,
            key,
            algorithms=["RS256"],
            issuer=settings.context_issuer,
            audience=settings.context_audience,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "jti"]},
        )
    except jwt.PyJWTError as error:
        raise AgentError("AI_CONTEXT_INVALID", "Invalid AI context", 401) from error
    if (
        claims.get("role") != "ADMIN"
        or claims.get("scope_level") != "SITE"
        or not claims.get("organization_id")
        or not isinstance(claims.get("site_ids"), list)
        or not claims.get("site_ids")
        or not claims.get("correlation_id")
        or not claims.get("deadline_at")
    ):
        raise AgentError("AI_CONTEXT_INVALID", "Incomplete AI context", 401)
    try:
        deadline = datetime.fromisoformat(claims["deadline_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise AgentError("AI_CONTEXT_INVALID", "Invalid deadline", 401) from error
    if deadline <= datetime.now(timezone.utc):
        raise AgentError("AI_DEADLINE_EXCEEDED", "Request deadline exceeded", 504, True)
    return claims
