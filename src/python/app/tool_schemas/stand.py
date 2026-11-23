"""Esquemas para herramientas de stands de feria."""

from __future__ import annotations


def tool_schema(name: str) -> dict:
    if name in {"createFairStand", "updateFairStand"}:
        properties = {
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
            "code": {"type": "string"},
            "description": {"type": "string"},
        }
        required = (
            ["fair_id", "code"]
            if name == "createFairStand"
            else ["fair_id", "stand_id"]
        )
        description = "Propone gestionar un stand de una feria."
    elif name == "deleteFairStand":
        properties = {
            "fair_id": {"type": "string"},
            "stand_id": {"type": "string"},
        }
        required = ["fair_id", "stand_id"]
        description = "Propone eliminar un stand."
    elif name == "listFairStands":
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Lista los stands de una feria autorizada."
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