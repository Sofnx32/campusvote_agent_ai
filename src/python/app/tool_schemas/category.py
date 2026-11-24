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
    elif name == "getFairRubric":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
        }
        required = ["fair_id"]
        description = "Consulta la rúbrica de una feria autorizada o de una de sus categorías."
    elif name == "setup_category_with_rubric":
        properties = {
            "fair_id": {"type": "string"},
            "category_name": {"type": "string"},
            "category_description": {"type": "string"},
            "rubric_name": {"type": "string"},
            "rubric_description": {"type": "string"},
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "points": {"type": "number"},
                        "position": {"type": "integer"},
                        "isActive": {"type": "boolean"},
                    },
                    "required": ["name", "points"],
                },
            },
        }
        required = ["fair_id", "category_name", "rubric_name", "criteria"]
        description = (
            "Crea de forma atómica una categoría, su rúbrica y criterios en una "
            "feria DRAFT; valida DRAFT y el presupuesto de 20 puntos antes de escribir."
        )
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