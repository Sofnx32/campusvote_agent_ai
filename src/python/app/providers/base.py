from __future__ import annotations

from abc import ABC, abstractmethod
from ..models import LLMResult


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, context: dict, messages: list[dict], allow_tools: bool = True) -> LLMResult:
        raise NotImplementedError