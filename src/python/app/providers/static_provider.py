from __future__ import annotations

from .base import LLMProvider
from ..models import LLMResult


class StaticProvider(LLMProvider):
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        return LLMResult(
            content="Puedo conversar contigo, consultar y listar información agregada de tus sedes asignadas."
        )