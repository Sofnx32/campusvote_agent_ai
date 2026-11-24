"""Respuestas conversacionales deterministas derivadas de herramientas de lectura.

Cuando el proveedor LLM devuelve ``content`` vacío después de que una lectura
ya se ejecutó correctamente, el turno no debe romperse con
``LLM_INVALID_RESPONSE``: existe evidencia verificable para responder.
Este módulo construye ese mensaje sin inventar datos y sin exponer UUIDs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# Lecturas que pueden originar una respuesta determinista. Se conserva
# ``getFairRubric`` por compatibilidad con el prompt del dominio.
DETERMINISTIC_READ_TOOLS = frozenset({
    "getSiteFairs",
    "listFairCategories",
    "listFairCategoryRubrics",
    "getFairRubric",
})

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def strip_uuids(text: str) -> str:
    """Elimina UUIDs de un texto antes de mostrarlo al usuario."""
    return _UUID_RE.sub("", text).strip()


def _selected_fair_name(context: Mapping | None) -> str:
    conv = (context or {}).get("conversation_context") or {}
    selected = conv.get("selected") if isinstance(conv, Mapping) else None
    fair = selected.get("fair") if isinstance(selected, Mapping) else None
    if isinstance(fair, Mapping):
        name = fair.get("name")
        if isinstance(name, str) and name.strip():
            return strip_uuids(name)
    for key in ("fair_name", "selected_fair_name"):
        name = (context or {}).get(key)
        if isinstance(name, str) and name.strip():
            return strip_uuids(name)
    return "la feria seleccionada"


def _extract_items(data: object) -> list[dict]:
    """Acepta listas directas y envelopes habituales del backend."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        for key in ("items", "categories", "fairs", "results", "rubrics", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _names(items: Sequence[Mapping], limit: int = 10) -> list[str]:
    names: list[str] = []
    for item in items:
        raw = item.get("name")
        if not isinstance(raw, str) or not raw.strip():
            continue
        clean = strip_uuids(raw)
        if clean and clean not in names:
            names.append(clean)
        if len(names) >= limit:
            break
    return names


def _bullet_list(names: Sequence[str]) -> str:
    return "\n".join(f"• **{name}**" for name in names)


def _tool_name(result: Mapping, index: int, names: Sequence[str]) -> str | None:
    """Resuelve el nombre de la herramienta aunque Node lo anide en ``meta``."""
    for candidate in (result.get("tool"), (result.get("meta") or {}).get("tool") if isinstance(result.get("meta"), Mapping) else None):
        if isinstance(candidate, str) and candidate:
            return candidate
    if index < len(names):
        return names[index]
    return None


def _categories_response(items: list[dict], fair_name: str) -> str:
    if not items:
        return (
            f"La feria {fair_name} aún no tiene categorías registradas. "
            "Dime el nombre de la categoría que deseas crear para comenzar."
        )
    names = _names(items)
    if not names:
        return f"La feria {fair_name} tiene {len(items)} categorías registradas. ¿Qué deseas hacer ahora?"
    return (
        f"La feria {fair_name} tiene {len(items)} "
        f"{'categoría' if len(items) == 1 else 'categorías'} registradas:\n"
        f"{_bullet_list(names)}\n\n¿Qué deseas hacer ahora?"
    )


def _fairs_response(items: list[dict]) -> str:
    drafts = [
        item for item in items
        if str(item.get("status") or item.get("state") or "").upper() == "DRAFT"
    ]
    if not drafts:
        return (
            "No hay ferias en borrador (DRAFT). "
            "¿Quieres crear una nueva feria para comenzar?"
        )
    names = _names(drafts)
    header = (
        f"Tengo {len(drafts)} feria{'s' if len(drafts) != 1 else ''} "
        "en borrador (DRAFT):"
    )
    body = _bullet_list(names) if names else "_(sin nombre)_"
    return f"{header}\n{body}\n\nIndícame cuál quieres configurar por su nombre."


def _rubrics_response(items: list[dict], fair_name: str) -> str:
    if not items:
        return (
            f"No encontré rúbricas registradas en {fair_name}. "
            "¿Quieres crear una indicando su nombre y criterios?"
        )
    names = _names(items)
    body = _bullet_list(names) if names else "_(sin nombre)_"
    return (
        f"En {fair_name} encontré {len(items)} "
        f"{'rúbrica' if len(items) == 1 else 'rúbricas'}:\n{body}\n\n"
        "¿Qué deseas hacer ahora?"
    )


def build_deterministic_response(
    results: Sequence[Mapping] | None,
    tool_names: Sequence[str] | None = None,
    context: Mapping | None = None,
) -> str | None:
    """Devuelve una respuesta conversacional o ``None`` si no hay evidencia.

    Se recorre el historial en orden inverso para usar la última lectura
    exitosa del turno. Solo se consideran resultados con ``success`` distinto
    de ``False`` y herramientas pertenecientes al conjunto de lectura.
    """
    if not results:
        return None
    names = list(tool_names or [])
    fair_name = _selected_fair_name(context)
    for index in range(len(results) - 1, -1, -1):
        result = results[index]
        if not isinstance(result, Mapping) or result.get("success") is False:
            continue
        tool_name = _tool_name(result, index, names)
        if tool_name not in DETERMINISTIC_READ_TOOLS:
            continue
        data = result.get("data")
        if not isinstance(data, (list, dict)) and isinstance(result.get("categories"), list):
            data = result["categories"]
        items = _extract_items(data)
        if tool_name == "listFairCategories":
            return _categories_response(items, fair_name)
        if tool_name == "getSiteFairs":
            return _fairs_response(items)
        if tool_name in {"listFairCategoryRubrics", "getFairRubric"}:
            return _rubrics_response(items, fair_name)
    return None
