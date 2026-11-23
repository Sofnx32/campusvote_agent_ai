"""Herramientas de lectura, escritura y lote para categorías y rúbricas (CampusVote).

Implementa las operaciones individuales y una herramienta atómica
(setup_category_with_rubric) que valida DRAFT y los 20 puntos antes de
crear nada, evitando dejar categorías huérfanas si algún criterio
excede el límite.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .rubrics_schemas import (
    BatchCategoryRubricInput,
    CreateCategoryInput,
    MAX_ACTIVE_RUBRIC_POINTS,
    RubricCriterionDraft,
    UpdateCategoryInput,
    ensure_fair_is_draft,
    validate_active_points_budget,
)


class CategoryRubricToolService:
    """Conecta las herramientas del agente con los endpoints de CampusVote."""

    def __init__(self, api_client: Any):
        self.api = api_client

    async def _get_draft_fair(self, fair_id: str) -> dict:
        fair = await self.api.get(f"/fairs/{fair_id}")
        fair_data = fair.get("data", fair)
        ensure_fair_is_draft(fair_data)
        return fair_data

    # --- LECTURA ---

    async def list_fair_categories(self, fair_id: str) -> dict:
        res = await self.api.get(f"/fairs/{fair_id}/categories")
        categories = res.get("data", res) if isinstance(res, dict) else res
        return {
            "success": True,
            "fair_id": fair_id,
            "count": len(categories),
            "categories": categories,
        }

    async def get_rubric(self, fair_id: str, category_id: Optional[str] = None) -> dict:
        endpoint = (
            f"/fairs/{fair_id}/categories/{category_id}/rubric"
            if category_id
            else f"/fairs/{fair_id}/rubric"
        )
        res = await self.api.get(endpoint)
        rubric = res.get("data", res) if isinstance(res, dict) else res
        criteria = rubric.get("criteria", []) if isinstance(rubric, dict) else []
        active_points = sum(c.get("points", 0) for c in criteria if c.get("isActive", True))
        return {
            "success": True,
            "scope": "CATEGORY" if category_id else "GLOBAL",
            "rubric": rubric,
            "active_points": active_points,
            "remaining_points": MAX_ACTIVE_RUBRIC_POINTS - active_points,
        }

    # --- ESCRITURA DE CATEGORÍAS (SOLO DRAFT) ---

    async def create_fair_category(
        self,
        fair_id: str,
        name: str,
        description: Optional[str] = None,
    ) -> dict:
        payload = CreateCategoryInput(fair_id=fair_id, name=name, description=description)
        await self._get_draft_fair(payload.fair_id)

        existing = await self.list_fair_categories(payload.fair_id)
        if any(c.get("name", "").strip().lower() == payload.name.lower() for c in existing["categories"]):
            return {
                "success": False,
                "error": f"Ya existe una categoría llamada '{payload.name}' en esta feria.",
            }

        body = {"name": payload.name}
        if payload.description:
            body["description"] = payload.description

        created = await self.api.post(f"/fairs/{payload.fair_id}/categories", json=body)
        return {"success": True, "category": created.get("data", created)}

    async def update_fair_category(
        self,
        fair_id: str,
        category_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        payload = UpdateCategoryInput(
            fair_id=fair_id,
            category_id=category_id,
            name=name,
            description=description,
        )
        await self._get_draft_fair(payload.fair_id)

        body = {k: v for k, v in {"name": payload.name, "description": payload.description}.items() if v is not None}
        if not body:
            return {"success": False, "error": "Debes indicar al menos 'name' o 'description' para actualizar."}

        updated = await self.api.put(
            f"/fairs/{payload.fair_id}/categories/{payload.category_id}",
            json=body,
        )
        return {"success": True, "category": updated.get("data", updated)}

    async def delete_fair_category(
        self,
        fair_id: str,
        category_id: str,
        confirmed: bool = False,
    ) -> dict:
        if not confirmed:
            return {
                "success": False,
                "requires_confirmation": True,
                "message": (
                    "Confirma explícitamente si deseas eliminar esta categoría. "
                    "Solo puede borrarse en estado DRAFT y si no tiene proyectos ni jurados asignados."
                ),
            }

        await self._get_draft_fair(fair_id)
        await self.api.delete(f"/fairs/{fair_id}/categories/{category_id}")
        return {"success": True, "deleted_category_id": category_id}

    # --- ESCRITURA DE RÚBRICAS Y CRITERIOS (SOLO DRAFT) ---

    async def create_rubric(
        self,
        fair_id: str,
        name: str,
        description: Optional[str] = None,
        category_id: Optional[str] = None,
    ) -> dict:
        await self._get_draft_fair(fair_id)
        endpoint = (
            f"/fairs/{fair_id}/categories/{category_id}/rubric"
            if category_id
            else f"/fairs/{fair_id}/rubric"
        )
        body = {"name": name.strip()}
        if description:
            body["description"] = description.strip()

        created = await self.api.post(endpoint, json=body)
        return {
            "success": True,
            "scope": "CATEGORY" if category_id else "GLOBAL",
            "rubric": created.get("data", created),
            "remaining_points": MAX_ACTIVE_RUBRIC_POINTS,
        }

    async def add_rubric_criterion(
        self,
        fair_id: str,
        name: str,
        points: int,
        description: Optional[str] = None,
        position: Optional[int] = None,
        is_active: bool = True,
        category_id: Optional[str] = None,
    ) -> dict:
        await self._get_draft_fair(fair_id)
        criterion = RubricCriterionDraft(
            name=name,
            description=description,
            points=points,
            position=position,
            isActive=is_active,
        )

        current = await self.get_rubric(fair_id=fair_id, category_id=category_id)
        existing_criteria = current["rubric"].get("criteria", [])
        budget = validate_active_points_budget(
            existing_criteria=existing_criteria,
            new_points=criterion.points,
            is_active=criterion.isActive,
        )

        endpoint = (
            f"/fairs/{fair_id}/categories/{category_id}/rubric/criteria"
            if category_id
            else f"/fairs/{fair_id}/rubric/criteria"
        )
        body: Dict[str, Any] = {
            "name": criterion.name,
            "points": criterion.points,
            "isActive": criterion.isActive,
        }
        if criterion.description:
            body["description"] = criterion.description
        if criterion.position is not None:
            body["position"] = criterion.position

        created = await self.api.post(endpoint, json=body)
        return {
            "success": True,
            "criterion": created.get("data", created),
            **budget,
        }

    # --- CREACIÓN EN LOTE (CATEGORÍA + RÚBRICA + CRITERIOS) ---

    async def setup_category_with_rubric(
        self,
        fair_id: str,
        category_name: str,
        rubric_name: str,
        criteria: List[dict],
        category_description: Optional[str] = None,
        rubric_description: Optional[str] = None,
    ) -> dict:
        # 1. Valida esquema, suma <= 20 pts y posiciones únicas ANTES de tocar la API
        batch = BatchCategoryRubricInput(
            fair_id=fair_id,
            category_name=category_name,
            category_description=category_description,
            rubric_name=rubric_name,
            rubric_description=rubric_description,
            criteria=[RubricCriterionDraft(**c) for c in criteria],
        )

        # 2. Crea categoría (valida DRAFT y nombre único)
        cat_res = await self.create_fair_category(
            fair_id=batch.fair_id,
            name=batch.category_name,
            description=batch.category_description,
        )
        if not cat_res.get("success"):
            return cat_res

        category = cat_res["category"]
        category_id = category["id"]

        # 3. Crea rúbrica por categoría
        rubric_res = await self.create_rubric(
            fair_id=batch.fair_id,
            category_id=category_id,
            name=batch.rubric_name,
            description=batch.rubric_description,
        )

        # 4. Agrega cada criterio con position auto-secuencial si no viene definido
        created_criteria = []
        for idx, item in enumerate(batch.criteria, start=1):
            crit_res = await self.add_rubric_criterion(
                fair_id=batch.fair_id,
                category_id=category_id,
                name=item.name,
                description=item.description,
                points=item.points,
                position=item.position or idx,
                is_active=item.isActive,
            )
            created_criteria.append(crit_res["criterion"])

        active_total = sum(c.points for c in batch.criteria if c.isActive)
        return {
            "success": True,
            "category": category,
            "rubric": rubric_res["rubric"],
            "criteria": created_criteria,
            "active_points": active_total,
            "remaining_points": MAX_ACTIVE_RUBRIC_POINTS - active_total,
        }