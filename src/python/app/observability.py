from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

request_id_context: ContextVar[str] = ContextVar("campusvote_request_id", default="-")
logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Telemetría correlacionada de un request (sin datos sensibles)."""

    request_id: str = "-"
    started: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    retries: int = 0
    fallbacks: int = 0
    rate_limited_429: int = 0
    timeouts: int = 0
    provider_latency_ms: float = 0.0
    tool_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    truncated_responses: int = 0
    tool_calls: int = 0

    def record_usage(self, usage: dict | None) -> None:
        if not isinstance(usage, dict):
            return
        for source, target in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(self, target, getattr(self, target) + value)

    def to_payload(self) -> dict:
        return {
            "event": "request_metrics",
            "requestId": self.request_id,
            "correlation_id": self.request_id,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "latency_ms": {
                "total": round((time.monotonic() - self.started) * 1000, 2),
                "provider": round(self.provider_latency_ms, 2),
                "tools": round(self.tool_latency_ms, 2),
            },
            "counts": {
                "llm_calls": self.llm_calls,
                "retries": self.retries,
                "fallbacks": self.fallbacks,
                "rate_limited_429": self.rate_limited_429,
                "timeouts": self.timeouts,
                "truncated_responses": self.truncated_responses,
                "tool_calls": self.tool_calls,
            },
        }


metrics_context: ContextVar[RequestMetrics | None] = ContextVar("campusvote_request_metrics", default=None)


def current_metrics() -> RequestMetrics | None:
    return metrics_context.get()


def log_request_metrics(metrics: RequestMetrics | None = None) -> dict | None:
    metrics = metrics or current_metrics()
    if metrics is None:
        return None
    payload = metrics.to_payload()
    logger.info(json.dumps(payload, ensure_ascii=False))
    return payload
