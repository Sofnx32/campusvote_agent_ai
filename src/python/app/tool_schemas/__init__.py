"""Tool schemas package - esquemas JSON para las herramientas del agente."""

from .academic import tool_schema as academic_tool_schema
from .fair import tool_schema as fair_tool_schema
from .rubric import tool_schema as rubric_tool_schema
from .category import tool_schema as category_tool_schema
from .stand import tool_schema as stand_tool_schema
from .core import tool_schema as core_tool_schema

# Mapeo de herramienta -> módulo
_TOOL_MODULE_MAP = {
    # Académico
    "createTeachingCriterion": "academic",
    "updateTeachingCriterion": "academic",
    "toggleTeachingCriterion": "academic",
    "deleteTeachingCriterion": "academic",
    "listTeachingCriteria": "academic",
    # Feria
    "createFair": "fair",
    "updateFair": "fair",
    "changeFairStatus": "fair",
    # Rúbrica
    "createFairRubric": "rubric",
    "addFairRubricCriterion": "rubric",
    "createCategoryFairRubric": "rubric",
    "addCategoryFairRubricCriterion": "rubric",
    # Categoría
    "createFairCategory": "category",
    "updateFairCategory": "category",
    "deleteFairCategory": "category",
    "listFairCategories": "category",
    "listFairCategoryRubrics": "category",
    "getFairRubric": "category",
    "setup_category_with_rubric": "category",
    # Stand
    "createFairStand": "stand",
    "updateFairStand": "stand",
    "deleteFairStand": "stand",
    "listFairStands": "stand",
    # Core (sitio, proyectos, resultados, etc.)
    "getSiteOverview": "core",
    "getSiteFairs": "core",
    "getSiteProjects": "core",
    "getSiteStands": "core",
    "getSiteResults": "core",
}

_MODULE_SCHEMAS = {
    "academic": academic_tool_schema,
    "fair": fair_tool_schema,
    "rubric": rubric_tool_schema,
    "category": category_tool_schema,
    "stand": stand_tool_schema,
    "core": core_tool_schema,
}


def _harden_schema(node: dict) -> dict:
    """Asegura tipos estrictos y `items` en todos los arrays anidados."""
    if node.get("type") == "array":
        if not isinstance(node.get("items"), dict):
            node["items"] = {"type": "string"}
        else:
            node["items"] = _harden_schema(node["items"])
    if node.get("type") == "object":
        node.setdefault("additionalProperties", False)
        properties = node.get("properties")
        if isinstance(properties, dict):
            for key, value in list(properties.items()):
                if isinstance(value, dict):
                    properties[key] = _harden_schema(value)
    return node


def tool_schema(name: str) -> dict:
    """Obtiene el esquema JSON para una herramienta por nombre."""
    module_name = _TOOL_MODULE_MAP.get(name, "core")
    schema_func = _MODULE_SCHEMAS[module_name]
    schema = schema_func(name)
    parameters = schema.get("parameters")
    if isinstance(parameters, dict):
        schema["parameters"] = _harden_schema(parameters)
    return schema


# Exportar para compatibilidad
__all__ = ["tool_schema"]