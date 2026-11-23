"""Esquemas para herramientas de categorías de feria."""

from __future__ import annotations


def tool_schema(name: str) -> dict:
    if name == "createFairCategory":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "name"]
        description = "Crea una categoría de feria en estado DRAFT."
    elif name == "updateFairCategory":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "category_id"]
        description = "Actualiza una categoría de feria en estado DRAFT."
    elif name == "deleteFairCategory":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
            "confirmed": {"type": "boolean"},
        }
        required = ["fair_id", "category_id", "confirmed"]
        description = "Elimina una categoría de feria en estado DRAFT (requiere confirmación)."
    elif name in {"listFairCategories", "listFairCategoryRubrics"}:
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Consulta las categorías o rúbricas por categoría de una feria autorizada."
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