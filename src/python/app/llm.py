from __future__ import annotations

from .config import settings
from .providers.base import LLMProvider
from .providers.static_provider import StaticProvider
from .providers.openai_compatible import OpenAICompatibleProvider


def get_provider() -> LLMProvider:
    return StaticProvider() if settings.llm_provider == "static" else OpenAICompatibleProvider()