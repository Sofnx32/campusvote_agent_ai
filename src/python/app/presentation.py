from __future__ import annotations

import json
import re

RAW_PAYLOAD_FALLBACK = (
    "Resumí la información de CampusVote. Si necesitas un detalle específico, "
    "dímelo y lo reviso."
)

_JSON_FENCE = re.compile(r"```(?:json)?\s*[\[{].*?```", re.IGNORECASE | re.DOTALL)
_OBJECT_MARKER = "[object Object]"


def _is_json_payload(text: str) -> bool:
    try:
        json.loads(text)
    except (TypeError, ValueError):
        return False
    return True


def sanitize_assistant_content(content: str) -> str:
    """Impide que un volcado técnico llegue al chat.

    El modelo debe resumir los resultados de las herramientas. Si aun así
    devuelve un JSON completo, una marca ``[object Object]`` o un bloque de
    código con un payload, se reemplaza por una respuesta conversacional.
    """
    text = (content or "").strip()
    if not text:
        return content
    if _OBJECT_MARKER in text:
        return RAW_PAYLOAD_FALLBACK
    if text[0] in "{[" and _is_json_payload(text):
        return RAW_PAYLOAD_FALLBACK

    without_fences = _JSON_FENCE.sub("", text).strip()
    if without_fences != text:
        return without_fences if re.search(r"\w{2,}", without_fences) else RAW_PAYLOAD_FALLBACK
    return text
