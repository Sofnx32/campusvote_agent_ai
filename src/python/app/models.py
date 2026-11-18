from __future__ import annotations

import json

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    correlation_id: str


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict


class LLMResult(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Telemetría de Fase 1: el proveedor la rellena y el orquestador la agrega.
    usage: dict = Field(default_factory=dict)
    latency_ms: float = 0.0
    truncated: bool = False

    def assistant_message(self) -> dict:
        """Build the assistant message that must precede tool results."""
        message: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in self.tool_calls
            ]
        return message
