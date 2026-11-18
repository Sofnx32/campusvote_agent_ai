"""Selección del parámetro de límite de salida compatible con el proveedor."""

from __future__ import annotations

DEFAULT_MAX_OUTPUT_TOKENS = 2000


def output_token_param(configured: str, model: str) -> str:
    """Devuelve `max_tokens` o `max_completion_tokens` según proveedor/modelo.

    - Valores explícitos se respetan tal cual.
    - `auto` usa `max_completion_tokens` para modelos recientes de OpenAI
      (gpt-5, o1/o3/o4, codex) y `max_tokens` para el resto (incluido Gemini).
    - Configuración desconocida cae a `max_tokens`, opción segura.
    """
    value = (configured or "auto").strip().lower()
    if value in {"max_tokens", "max_completion_tokens"}:
        return value
    normalized = (model or "").lower()
    if value == "auto" and (
        normalized.startswith(("gpt-5", "o1", "o3", "o4")) or "codex" in normalized
    ):
        return "max_completion_tokens"
    return "max_tokens"
