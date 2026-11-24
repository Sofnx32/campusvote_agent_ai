"""Registro de herramientas expuestas al proveedor (Fase 1: sin filtrar)."""

from __future__ import annotations

TOOLS = [
    "getSiteOverview", "getSiteFairs", "getSiteProjects", "getSiteStands", "getSiteResults",
    "listTeachingCriteria", "createTeachingCriterion", "updateTeachingCriterion",
    "toggleTeachingCriterion", "deleteTeachingCriterion",
    "listFairCategories", "listFairCategoryRubrics", "getFairRubric",
    "createFairRubric", "addFairRubricCriterion",
    "createCategoryFairRubric", "addCategoryFairRubricCriterion",
    "createFairCategory", "updateFairCategory", "deleteFairCategory",
    "setup_category_with_rubric",
    "createFair", "updateFair", "changeFairStatus",
    "listFairStands", "createFairStand", "updateFairStand", "deleteFairStand",
]

READ_TOOLS = frozenset({
    "getSiteOverview", "getSiteFairs", "getSiteProjects", "getSiteStands", "getSiteResults",
    "listFairStands", "listFairCategories", "listFairCategoryRubrics", "getFairRubric",
    "listTeachingCriteria",
})

WRITE_TOOLS = frozenset(TOOLS) - READ_TOOLS
