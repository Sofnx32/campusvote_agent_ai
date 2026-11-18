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


_TITLE_BY_TOOL = {
    "createFairRubric": "Crear rúbrica",
    "addFairRubricCriterion": "Agregar criterio de rúbrica",
    "createCategoryFairRubric": "Crear rúbrica por categoría",
    "addCategoryFairRubricCriterion": "Agregar criterio por categoría",
    "createTeachingCriterion": "Crear criterio de evaluación docente",
    "updateTeachingCriterion": "Actualizar criterio de evaluación docente",
    "toggleTeachingCriterion": "Cambiar estado del criterio docente",
    "deleteTeachingCriterion": "Eliminar criterio de evaluación docente",
    "updateFair": "Actualizar feria",
    "changeFairStatus": "Cambiar estado de feria",
    "createFairStand": "Crear stand",
    "updateFairStand": "Actualizar stand",
    "deleteFairStand": "Eliminar stand",
}

_FIELD_LABELS = {
    "name": "Nombre", "description": "Descripción", "code": "Código del stand",
    "min_score": "Puntaje mínimo", "max_score": "Puntaje máximo", "position": "Posición",
    "points": "Puntos",
    "status": "Estado", "starts_at": "Inicio (fecha y hora)", "ends_at": "Fin (fecha y hora)",
    "registration_deadline": "Cierre de inscripción (fecha y hora)",
}

_FAIR_FIELDS = [
    {"name": "name", "type": "text", "label": "Nombre", "required": True, "max_length": 200},
    {"name": "description", "type": "textarea", "label": "Descripción", "required": False, "max_length": 5000},
    {"name": "starts_at", "type": "datetime", "label": "Inicio (fecha y hora)", "required": False},
    {"name": "ends_at", "type": "datetime", "label": "Fin (fecha y hora)", "required": False},
    {"name": "registration_deadline", "type": "datetime", "label": "Cierre de inscripción", "required": False},
    {"name": "image_url", "type": "image", "label": "Portada", "required": True},
]


def _field_type(key: str) -> str:
    if key in {"min_score", "max_score", "position"}:
        return "number"
    if key in {"starts_at", "ends_at", "registration_deadline"}:
        return "datetime"
    if key == "status":
        return "select"
    return "textarea" if key == "description" else "text"


def _confirmation_ui(action: dict) -> tuple[dict, dict]:
    proposal = action.get("proposal", {})
    if action.get("tool") == "createFair":
        fields, title = _FAIR_FIELDS, "Crear feria"
    else:
        fields = [
            {
                "name": key,
                "type": _field_type(key),
                "label": _FIELD_LABELS.get(key, key.replace("_", " ").capitalize()),
                "required": False,
                **({"options": [{"value": status, "label": status} for status in ["DRAFT", "OPEN", "CLOSED"]]} if key == "status" else {}),
                **({"max_length": 5000} if key == "description" else {}),
            }
            for key in proposal if not key.endswith("_id")
        ]
        title = _TITLE_BY_TOOL.get(action.get("tool"), "Revisar operación")
    ui = {
        "type": "confirmation",
        "schema": {"title": title, "fields": fields},
        "data": {
            **{key: value for key, value in proposal.items() if not key.endswith("_id")},
            "site_name": action.get("site_name"),
        },
        "actions": ["confirm", "edit", "cancel"],
    }
    operation = {
        "id": action.get("operation_id"),
        "version": action.get("version"),
        "status": "awaiting_confirmation",
    }
    return ui, operation


def build_ui(last_action: dict | None) -> tuple[dict, dict | None]:
    """Construye la tarjeta UI y la operación derivada del último tool result."""
    if isinstance(last_action, dict) and last_action.get("status") == "confirmation_required":
        return _confirmation_ui(last_action)
    if isinstance(last_action, dict) and last_action.get("status") == "created":
        # Node construye la tarjeta de éxito; Python no reenvía el registro crudo.
        return (
            {"type": "message", "schema": None, "data": None, "actions": []},
            {
                "id": last_action.get("operation_id"),
                "version": last_action.get("version"),
                "status": "completed",
            },
        )
    return {"type": "message", "schema": None, "data": None, "actions": []}, None
