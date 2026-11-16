from __future__ import annotations


def tool_schema(name: str) -> dict:
    if name == "createTeachingCriterion":
        properties = {"name": {"type": "string"}, "description": {"type": "string"}}
        required = ["name"]
        description = "Propone un criterio general de evaluación docente para la sede activa."
    elif name == "updateTeachingCriterion":
        properties = {"criterion_id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}}
        required = ["criterion_id"]
        description = "Propone actualizar un criterio existente usando su ID del listado autorizado."
    elif name in {"toggleTeachingCriterion", "deleteTeachingCriterion"}:
        properties = {"criterion_id": {"type": "string"}}
        required = ["criterion_id"]
        description = "Propone cambiar el estado o eliminar un criterio usando su ID del listado autorizado."
    elif name == "createFairRubric":
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
    elif name == "createFair":
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
    elif name == "listFairStands":
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Lista los stands de una feria autorizada."
    elif name in {"listFairCategories", "listFairCategoryRubrics"}:
        properties = {"fair_id": {"type": "string"}}
        required = ["fair_id"]
        description = "Consulta las categorías o rúbricas por categoría de una feria autorizada."
    elif name in {"createFairStand", "updateFairStand"}:
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
    elif name == "listTeachingCriteria":
        properties = {}
        required = []
        description = "Lista los criterios de evaluación docente configurados para la sede activa."
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