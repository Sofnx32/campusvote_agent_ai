from __future__ import annotations

import json
from typing import FrozenSet

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    correlation_id: str


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict
    # Allowlist inmutable del ciclo LLM que originó esta llamada.
    # Se propaga desde LLMResult para validación pre-ejecución sin depender
    # de estado global mutable ni recálculos posteriores.
    allowed_tools: FrozenSet[str] | None = None


class LLMResult(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Allowlist inmutable de las herramientas anunciadas en ESTE ciclo LLM.
    # Debe coincidir exactamente con lo enviado al proveedor (schemas).
    # Se recalcula en cada vuelta del bucle (nuevo ciclo = nueva allowlist).
    allowed_tools: FrozenSet[str] = frozenset()
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
