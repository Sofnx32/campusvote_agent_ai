"""Esquemas para herramientas de rúbricas."""

from __future__ import annotations


def tool_schema(name: str) -> dict:
    if name == "createFairRubric":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "name"]
        description = "Propone una rúbrica para una feria en borrador."
    elif name == "addFairRubricCriterion":
        properties = {
            "fair_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "min_score": {"type": "number"},
            "max_score": {"type": "number"},
            "position": {"type": "integer"},
        }
        required = ["fair_id", "name", "min_score", "max_score"]
        description = "Propone agregar un criterio a una rúbrica de feria."
    elif name == "createCategoryFairRubric":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        }
        required = ["fair_id", "category_id", "name"]
        description = "Propone una rúbrica para una categoría de feria en borrador."
    elif name == "addCategoryFairRubricCriterion":
        properties = {
            "fair_id": {"type": "string"},
            "category_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "points": {"type": "number"},
            "position": {"type": "integer"},
        }
        required = ["fair_id", "category_id", "name", "points"]
        description = "Propone agregar un criterio a una rúbrica de categoría; la suma activa se limita a 20 puntos."
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