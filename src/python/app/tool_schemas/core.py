"""Esquemas para herramientas core (sitio, proyectos, resultados)."""

from __future__ import annotations


def tool_schema(name: str) -> dict:
    # Estas son herramientas de solo lectura para consultas agregadas
    read_tools = {
        "getSiteOverview",
        "getSiteFairs",
        "getSiteProjects",
        "getSiteStands",
        "getSiteResults",
    }

    if name in read_tools:
        properties = {}
        required = []
        description = f"Consulta y lista datos agregados de la sede activa usando {name}."
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