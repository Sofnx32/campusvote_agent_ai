"""Esquemas para herramientas de feria."""

from __future__ import annotations


def tool_schema(name: str) -> dict:
    if name == "createFair":
        properties = {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "image_url": {"type": "string"},
            "starts_at": {"type": "string", "description": "ISO 8601 con hora y zona, ej. 2026-11-20T08:00:00-05:00"},
            "ends_at": {"type": "string", "description": "ISO 8601 con hora y zona, ej. 2026-11-22T18:00:00-05:00"},
            "registration_deadline": {"type": "string", "description": "ISO 8601 con hora y zona, ej. 2026-11-19T23:59:00-05:00"},
        }
        required = ["name"]
        description = "Propone una feria. No inventes image_url; la tarjeta permite subir la portada autenticadamente."
    elif name == "updateFair":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "starts_at": {"type": "string", "description": "ISO 8601 con hora y zona, ej. 2026-11-20T08:00:00-05:00"},
            "ends_at": {"type": "string", "description": "ISO 8601 con hora y zona, ej. 2026-11-22T18:00:00-05:00"},
        }
        required = ["fair_id"]
        description = "Propone actualizar una feria autorizada."
    elif name == "changeFairStatus":
        properties = {
            "fair_id": {"type": "string"},
            "status": {"type": "string", "enum": ["DRAFT", "OPEN", "CLOSED"]},
        }
        required = ["fair_id", "status"]
        description = "Propone cambiar el estado de una feria."
    else:
        properties = {}
        required = []
        description = f"Consulta y lista datos agregados de la sede activa usando {name}."
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }