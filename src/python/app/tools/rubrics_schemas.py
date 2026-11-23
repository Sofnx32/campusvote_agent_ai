"""Esquemas y validadores de negocio para categorías y rúbricas (CampusVote).

Centraliza la validación de puntajes (chk_fair_rubric_active_points_limit),
estados de feria (DRAFT) y unicidad antes de disparar peticiones POST/PUT
contra el backend principal.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

MAX_ACTIVE_RUBRIC_POINTS = 20


class CreateCategoryInput(BaseModel):
    fair_id: str = Field(..., min_length=1, description="ID de la feria en estado DRAFT")
    name: str = Field(..., min_length=2, max_length=120, description="Nombre único de la categoría dentro de la feria")
    description: Optional[str] = Field(None, max_length=500, description="Descripción opcional de la categoría")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("El nombre de la categoría no puede estar vacío.")
        return clean


class UpdateCategoryInput(BaseModel):
    fair_id: str = Field(..., min_length=1)
    category_id: str = Field(..., min_length=1)
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=500)


class RubricCriterionDraft(BaseModel):
    name: str = Field(..., min_length=2, max_length=150, description="Nombre del criterio de evaluación")
    description: Optional[str] = Field(None, max_length=500)
    points: int = Field(..., ge=1, le=MAX_ACTIVE_RUBRIC_POINTS, description="Puntaje asignado (1 a 20)")
    position: Optional[int] = Field(None, ge=1, description="Orden único; se auto-asigna si se omite")
    isActive: bool = Field(True, description="Si el criterio suma al límite activo de 20 puntos")


class BatchCategoryRubricInput(BaseModel):
    fair_id: str = Field(..., min_length=1)
    category_name: str = Field(..., min_length=2, max_length=120)
    category_description: Optional[str] = Field(None, max_length=500)
    rubric_name: str = Field(..., min_length=2, max_length=150)
    rubric_description: Optional[str] = Field(None, max_length=500)
    criteria: List[RubricCriterionDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_total_points_and_positions(self) -> "BatchCategoryRubricInput":
        active_points = sum(c.points for c in self.criteria if c.isActive)
        if active_points > MAX_ACTIVE_RUBRIC_POINTS:
            raise ValueError(
                f"Los criterios activos suman {active_points} puntos y superan el máximo permitido "
                f"de {MAX_ACTIVE_RUBRIC_POINTS} puntos por rúbrica."
            )
        explicit_positions = [c.position for c in self.criteria if c.position is not None]
        if len(explicit_positions) != len(set(explicit_positions)):
            raise ValueError("Las posiciones (position) de los criterios dentro de una rúbrica deben ser únicas.")
        return self


def ensure_fair_is_draft(fair: dict) -> None:
    status = (fair.get("status") or "").upper()
    if status != "DRAFT":
        raise ValueError(
            f"Operación no permitida: la feria '{fair.get('name', fair.get('id'))}' está en estado "
            f"{status or 'DESCONOCIDO'}. Las categorías y rúbricas solo pueden configurarse cuando la feria está en DRAFT."
        )


def validate_active_points_budget(
    existing_criteria: List[dict],
    new_points: int,
    is_active: bool = True,
) -> dict:
    current_active = sum(
        int(c.get("points", 0))
        for c in existing_criteria
        if c.get("isActive", True)
    )
    projected_active = current_active + (new_points if is_active else 0)
    remaining_before = MAX_ACTIVE_RUBRIC_POINTS - current_active

    if projected_active > MAX_ACTIVE_RUBRIC_POINTS:
        raise ValueError(
            f"No se puede agregar el criterio de {new_points} pts: la rúbrica ya tiene "
            f"{current_active}/{MAX_ACTIVE_RUBRIC_POINTS} pts activos (disponibles: {remaining_before} pts)."
        )

    return {
        "current_active_points": current_active,
        "projected_active_points": projected_active,
        "remaining_points": MAX_ACTIVE_RUBRIC_POINTS - projected_active,
    }