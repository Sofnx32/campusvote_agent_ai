"""Esquemas para herramientas de evaluación docente."""

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