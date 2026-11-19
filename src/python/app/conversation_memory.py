"""Contexto de sesión y resolución conservadora de referencias.

Nivel 2: Entidades seleccionadas en la conversación actual.
Nivel 3: Índice de menciones para resolver referencias anafóricas/ordinales.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Patrones para detectar referencias
_ORDINAL_PATTERN = re.compile(
    r"\b(primer[a]?|segund[a]?|tercer[a]?|cuart[a]?|quint[a]?|"
    r"1(?:er|ra)?|2(?:da|do)?|3(?:ra|er)?|4(?:ta|to)?|5(?:ta|to)?)\b",
    re.IGNORECASE,
)
_ANAPHORIC_PATTERN = re.compile(
    r"\b(esa|este|esta|ese|aquella|aquello|dicha|dicho|la|el|lo)\b",
    re.IGNORECASE,
)


def _normalize_entity_type(entity_type: str) -> str:
    """``fair_id`` -> ``fair``; el índice de menciones usa tipos sin sufijo."""
    return entity_type[:-3] if entity_type.endswith("_id") else entity_type


# Una lista de resultados solo es evidencia válida durante unas pocas rondas.
_REFERENCE_FRESHNESS_TURNS = 3

_ORDINAL_INDEX = {
    "primera": 0, "primer": 0, "1": 0, "1ra": 0, "1er": 0,
    "segunda": 1, "segundo": 1, "2": 1, "2da": 1, "2do": 1,
    "tercera": 2, "tercer": 2, "3": 2, "3ra": 2, "3er": 2,
    "cuarta": 3, "cuarto": 3, "4": 3, "4ta": 3, "4to": 3,
    "quinta": 4, "quinto": 4, "5": 4, "5ta": 4, "5to": 4,
}

# Solo estas herramientas producen listas válidas para resolución ordinal.
_RESULT_TOOL_BY_TYPE = {
    "fair": "getSiteFairs",
    "project": "getSiteProjects",
    "stand": "listFairStands",
    "criterion": "listTeachingCriteria",
    "category": "listFairCategories",
    "rubric": "listFairCategoryRubrics",
}


@dataclass(frozen=True)
class ResolvedReference:
    """Entidad resuelta con evidencia verificable (Etapa 2.3)."""

    type: str
    id: str
    name: str | None = None
    source: str = "selected"  # selected | mention | ordinal
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "details": self.details,
        }


@dataclass(frozen=True)
class AmbiguousReference:
    """Referencia con dos o más candidatos plausibles: no se resuelve."""

    type: str
    candidates: tuple[str, ...]
    reason: str = "multiple_candidates"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


@dataclass
class TurnContext:
    """Representación compacta y controlada del contexto de un turno.

    Los datos se tratan estrictamente como *untrusted data*: nunca como
    instrucciones. No contiene el historial completo ni la memoria entera.
    """

    resolved: list[ResolvedReference] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: list[AmbiguousReference] = field(default_factory=list)
    last_query: dict[str, Any] | None = None
    selected: dict[str, dict[str, Any]] = field(default_factory=dict)
    confirmation: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": [item.to_dict() for item in self.resolved],
            "unresolved": list(self.unresolved),
            "ambiguous": [item.to_dict() for item in self.ambiguous],
            "last_query": self.last_query,
            "selected": self.selected,
            "confirmation": self.confirmation,
        }


_ENTITY_KEYWORDS = {
    "feria": "fair_id",
    "ferias": "fair_id",
    "proyecto": "project_id",
    "proyectos": "project_id",
    "stand": "stand_id",
    "stands": "stand_id",
    "candidato": "candidate_id",
    "candidatos": "candidate_id",
    "candidata": "candidate_id",
    "candidatas": "candidate_id",
    "rúbrica": "rubric_id",
    "rubrica": "rubric_id",
    "categoría": "category_id",
    "categoria": "category_id",
    "criterio": "criterion_id",
    "criterios": "criterion_id",
}


@dataclass
class SessionContext:
    """Estado de la sesión conversacional (Nivel 2)."""

    selected_fair: dict[str, Any] | None = None      # {"id": "f-123", "name": "Feria Ciencias", "status": "DRAFT"}
    selected_project: dict[str, Any] | None = None
    selected_stand: dict[str, Any] | None = None
    selected_candidate: dict[str, Any] | None = None
    last_query_tool: str | None = None
    last_query_summary: str | None = None
    last_query_results: list[dict] | None = None     # Resultados completos de la última consulta
    last_query_turn: int | None = None               # Turno en que se obtuvieron esos resultados
    pending_operation: dict[str, Any] | None = None  # {"tool": "createFair", "operation_id": "...", "version": 1}
    confirmation_state: str = "none"                 # "awaiting_confirmation" | "none"

    def update_from_tool_result(self, tool_name: str, result: dict) -> None:
        """Actualiza el contexto basado en el resultado de una herramienta."""
        self.last_query_tool = tool_name

        if tool_name == "getSiteFairs" and isinstance(result.get("data"), list):
            fairs = result["data"]
            self.last_query_results = fairs
            count = len(fairs)
            states = {}
            for f in fairs:
                status = f.get("status", "UNKNOWN")
                states[status] = states.get(status, 0) + 1
            parts = [f"{v} {k.lower()}" for k, v in states.items()]
            self.last_query_summary = f"{count} ferias: {', '.join(parts)}"

        elif tool_name == "getSiteProjects" and isinstance(result.get("data"), list):
            projects = result["data"]
            self.last_query_results = projects
            self.last_query_summary = f"{len(projects)} proyectos"

        elif tool_name == "listFairStands" and isinstance(result.get("data"), list):
            stands = result["data"]
            self.last_query_results = stands
            self.last_query_summary = f"{len(stands)} stands"

        elif tool_name == "getSiteResults" and isinstance(result.get("data"), list):
            self.last_query_results = result["data"]
            self.last_query_summary = f"{len(result['data'])} resultados"

        elif tool_name in {"listTeachingCriteria", "listFairCategories", "listFairCategoryRubrics"}:
            data = result.get("data", [])
            self.last_query_results = data if isinstance(data, list) else []
            self.last_query_summary = f"{len(self.last_query_results)} elementos"

    def select_entity(self, entity_type: str, entity: dict[str, Any]) -> None:
        """Selecciona una entidad como activa en la conversación."""
        if entity_type == "fair":
            self.selected_fair = entity
        elif entity_type == "project":
            self.selected_project = entity
        elif entity_type == "stand":
            self.selected_stand = entity
        elif entity_type == "candidate":
            self.selected_candidate = entity

    def get_selected(self, entity_type: str) -> dict[str, Any] | None:
        """Obtiene la entidad seleccionada de un tipo."""
        return {
            "fair": self.selected_fair,
            "project": self.selected_project,
            "stand": self.selected_stand,
            "candidate": self.selected_candidate,
        }.get(entity_type)

    def clear_pending_operation(self) -> None:
        self.pending_operation = None
        self.confirmation_state = "none"


class EntityMentionIndex:
    """Índice de menciones de entidades para resolución de referencias (Nivel 3)."""

    def __init__(self):
        # entity_type -> entity_id -> [turn_indices]
        self._index: dict[str, dict[str, list[int]]] = {}

    def record_mention(self, entity_type: str, entity_id: str, turn_index: int) -> None:
        """Registra que una entidad fue mencionada en un turno."""
        if entity_type not in self._index:
            self._index[entity_type] = {}
        if entity_id not in self._index[entity_type]:
            self._index[entity_type][entity_id] = []
        self._index[entity_type][entity_id].append(turn_index)

    def record_from_tool_result(self, tool_name: str, result: dict, turn_index: int) -> None:
        """Extrae y registra entidades de un resultado de herramienta."""
        data = result.get("data")
        if not isinstance(data, list):
            return

        entity_type_map = {
            "getSiteFairs": "fair",
            "getSiteProjects": "project",
            "listFairStands": "stand",
            "getSiteResults": "project",  # los resultados son de proyectos
            "listTeachingCriteria": "criterion",
            "listFairCategories": "category",
            "listFairCategoryRubrics": "rubric",
        }
        entity_type = entity_type_map.get(tool_name)
        if not entity_type:
            return

        id_field_map = {
            "fair": "id",
            "project": "id",
            "stand": "id",
            "criterion": "id",
            "category": "id",
            "rubric": "id",
        }
        id_field = id_field_map.get(entity_type, "id")

        for item in data:
            if isinstance(item, dict) and id_field in item:
                self.record_mention(entity_type, str(item[id_field]), turn_index)

    def get_most_recent(self, entity_type: str, before_turn: int | None = None) -> str | None:
        """Obtiene la entidad más recientemente mencionada de un tipo."""
        if entity_type not in self._index:
            return None
        candidates = []
        for entity_id, turns in self._index[entity_type].items():
            for t in turns:
                if before_turn is None or t < before_turn:
                    candidates.append((t, entity_id))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def get_by_ordinal(self, entity_type: str, ordinal: str, before_turn: int | None = None) -> str | None:
        """Resuelve una referencia ordinal (primera, segunda, etc.) sobre resultados previos."""
        # Necesitamos acceder a los resultados ordenados de la última consulta
        # Esta función se usa junto con SessionContext.last_query_results
        return None  # La resolución ordinal se hace en ConversationMemory.resolve_reference


class ConversationMemory:
    """Gestor unificado de memoria conversacional (Niveles 2 y 3)."""

    def __init__(self):
        self.session = SessionContext()
        self.mentions = EntityMentionIndex()
        self._current_turn = 0

    def start_turn(self) -> None:
        """Marca el inicio de un nuevo turno."""
        self._current_turn += 1

    def end_turn(
        self,
        user_message: str,
        assistant_message: str,
        tool_calls: list[dict] | None,
        tool_results: list[dict] | None,
        entities: dict[str, str] | None,
    ) -> None:
        """Procesa el final de un turno: actualiza contexto e índice."""
        # Registrar entidades del mensaje del usuario (referencias explícitas)
        if entities:
            for entity_type, entity_id in entities.items():
                self.mentions.record_mention(
                    _normalize_entity_type(entity_type), entity_id, self._current_turn
                )

        # Registrar entidades de resultados de herramientas
        if tool_results:
            for result in tool_results:
                tool_name = result.get("tool")
                if tool_name:
                    self.mentions.record_from_tool_result(tool_name, result, self._current_turn)
                    self.session.update_from_tool_result(tool_name, result)
                    self.session.last_query_turn = self._current_turn

        # Detectar selección de entidad en la respuesta del assistant
        self._detect_entity_selection(assistant_message)

    def _detect_entity_selection(self, assistant_message: str) -> None:
        """Detecta si el assistant menciona/selecciona una entidad específica."""
        # Patrones como "La feria 'Feria Ciencias' (ID: f-123)...".
        # El grupo de nombre es no codicioso para no capturar IDs de entidades
        # posteriores cuando un mismo mensaje menciona varias entidades.
        fair_match = re.search(r"feria\s+['\"]?([^'\"]+?)['\"]?\s*\(?\s*ID:\s*([a-z0-9-]+)", assistant_message, re.IGNORECASE)
        if fair_match:
            name, fair_id = fair_match.groups()
            self.session.select_entity("fair", {"id": fair_id, "name": name.strip()})
            self.mentions.record_mention("fair", fair_id, self._current_turn)

        project_match = re.search(r"proyecto\s+['\"]?([^'\"]+?)['\"]?\s*\(?\s*ID:\s*([a-z0-9-]+)", assistant_message, re.IGNORECASE)
        if project_match:
            name, project_id = project_match.groups()
            self.session.select_entity("project", {"id": project_id, "name": name.strip()})
            self.mentions.record_mention("project", project_id, self._current_turn)

        stand_match = re.search(r"stand\s+['\"]?([^'\"]+?)['\"]?\s*\(?\s*ID:\s*([a-z0-9-]+)", assistant_message, re.IGNORECASE)
        if stand_match:
            name, stand_id = stand_match.groups()
            self.session.select_entity("stand", {"id": stand_id, "name": name.strip()})
            self.mentions.record_mention("stand", stand_id, self._current_turn)

        candidate_match = re.search(r"candidat[oa]\s+['\"]?([^'\"]+?)['\"]?\s*\(?\s*ID:\s*([a-z0-9-]+)", assistant_message, re.IGNORECASE)
        if candidate_match:
            name, candidate_id = candidate_match.groups()
            self.session.select_entity("candidate", {"id": candidate_id, "name": name.strip()})
            self.mentions.record_mention("candidate", candidate_id, self._current_turn)

    def resolve_reference(self, user_message: str) -> dict[str, Any] | None:
        """Resuelve referencias anafóricas/ordinales en el mensaje del usuario.

        Retorna dict con la entidad resuelta o None si hay ambigüedad.
        """
        msg_lower = user_message.lower()

        # 1. Referencia ordinal explícita: "la segunda feria", "el primer proyecto"
        ordinal_match = _ORDINAL_PATTERN.search(msg_lower)
        entity_keyword_match = None
        for keyword, entity_type in _ENTITY_KEYWORDS.items():
            if keyword in msg_lower:
                entity_keyword_match = (keyword, entity_type)
                break

        if ordinal_match and entity_keyword_match:
            ordinal_word = ordinal_match.group(1).lower()
            entity_type = entity_keyword_match[1]

            # Convertir ordinal a índice (0-based)
            ordinal_map = {
                "primera": 0, "primer": 0, "1": 0, "1ra": 0, "1er": 0,
                "segunda": 1, "segundo": 1, "2": 1, "2da": 1, "2do": 1,
                "tercera": 2, "tercer": 2, "3": 2, "3ra": 2, "3er": 2,
                "cuarta": 3, "cuarto": 3, "4": 3, "4ta": 3, "4to": 3,
                "quinta": 4, "quinto": 4, "5": 4, "5ta": 4, "5to": 4,
            }
            idx = ordinal_map.get(ordinal_word)
            if idx is not None and self.session.last_query_results:
                # Filtrar resultados por tipo de entidad
                results = self.session.last_query_results
                if entity_type == "fair_id" and all("status" in r for r in results):
                    # Es una feria
                    if idx < len(results):
                        fair = results[idx]
                        return {"type": "fair", "id": fair.get("id"), "data": fair}
                elif entity_type == "project_id":
                    if idx < len(results):
                        project = results[idx]
                        return {"type": "project", "id": project.get("id"), "data": project}
                elif entity_type == "stand_id":
                    if idx < len(results):
                        stand = results[idx]
                        return {"type": "stand", "id": stand.get("id"), "data": stand}

        # 2. Referencia a stands de "esa feria" / "la feria" (ANTES que keywords generales)
        # Esto evita que "feria" en "stands de esa feria" active el keyword fair_id
        if "stand" in msg_lower and ("esa" in msg_lower or "la feria" in msg_lower):
            if self.session.selected_fair and self.session.last_query_results:
                if self.session.last_query_tool == "listFairStands":
                    return {"type": "stands_list", "fair_id": self.session.selected_fair["id"], "data": self.session.last_query_results}

        # 3. Referencia anafórica + entidad: "esa feria", "ese proyecto"
        anaphoric_match = _ANAPHORIC_PATTERN.search(msg_lower)
        if anaphoric_match and entity_keyword_match:
            entity_type = entity_keyword_match[1]
            # Intentar resolver desde entidad seleccionada (Nivel 2)
            selected = self.session.get_selected(entity_type.replace("_id", ""))
            if selected and selected.get("id"):
                return {"type": entity_type.replace("_id", ""), "id": selected["id"], "data": selected}

            # Fallback: mención más reciente en el índice (Nivel 3)
            recent_id = self.mentions.get_most_recent(entity_type.replace("_id", ""), self._current_turn)
            if recent_id and self.session.last_query_results:
                for item in self.session.last_query_results:
                    if str(item.get("id")) == recent_id:
                        return {"type": entity_type.replace("_id", ""), "id": recent_id, "data": item}

        # 4. Solo entidad sin anáfora/ordinal pero con selección activa: "la feria"
        if entity_keyword_match and not ordinal_match and not anaphoric_match:
            entity_type = entity_keyword_match[1]
            selected = self.session.get_selected(entity_type.replace("_id", ""))
            if selected and selected.get("id"):
                return {"type": entity_type.replace("_id", ""), "id": selected["id"], "data": selected}

        return None  # Ambigüedad: no resolver

    # --- Integración con el turno (Etapa 2.3) --------------------------------

    def _last_query_is_fresh(self) -> bool:
        """Una lista previa solo es evidencia válida en una ventana corta."""
        turn = self.session.last_query_turn
        if turn is None or turn > self._current_turn:
            return False
        return (self._current_turn - turn) <= _REFERENCE_FRESHNESS_TURNS

    @staticmethod
    def _entity_types_in(message: str) -> list[str]:
        found: list[str] = []
        for keyword, entity_type in _ENTITY_KEYWORDS.items():
            if re.search(r"\b" + re.escape(keyword) + r"\b", message):
                normalized = _normalize_entity_type(entity_type)
                if normalized not in found:
                    found.append(normalized)
        return found

    def _recent_candidate_ids(self, entity_type: str) -> list[str]:
        """Candidatos con evidencia reciente, sin duplicados ni invención."""
        candidates: list[tuple[int, str]] = []
        for entity_id, turns in self.mentions._index.get(entity_type, {}).items():
            recent = [
                turn for turn in turns
                if turn <= self._current_turn and self._current_turn - turn <= _REFERENCE_FRESHNESS_TURNS
            ]
            if recent:
                candidates.append((max(recent), str(entity_id)))

        expected_tool = _RESULT_TOOL_BY_TYPE.get(entity_type)
        if expected_tool and self.session.last_query_tool == expected_tool and self._last_query_is_fresh():
            for item in self.session.last_query_results or []:
                if isinstance(item, dict) and item.get("id"):
                    candidates.append((self.session.last_query_turn or 0, str(item["id"])))

        candidates.sort(reverse=True)
        unique: list[str] = []
        for _, entity_id in candidates:
            if entity_id not in unique:
                unique.append(entity_id)
        return unique

    def _resolve_ordinal(self, message: str, entity_type: str) -> ResolvedReference | None:
        """Resuelve ordinales solo sobre una lista reciente y del tipo correcto."""
        match = _ORDINAL_PATTERN.search(message)
        if not match:
            return None
        index = _ORDINAL_INDEX.get(match.group(1).lower())
        expected_tool = _RESULT_TOOL_BY_TYPE.get(entity_type)
        if index is None or expected_tool is None:
            return None
        if self.session.last_query_tool != expected_tool or not self._last_query_is_fresh():
            return None
        results = self.session.last_query_results or []
        if index >= len(results):
            return None
        item = results[index]
        if not isinstance(item, dict) or not item.get("id"):
            return None
        if entity_type == "fair" and "status" not in item:
            return None
        return ResolvedReference(
            type=entity_type,
            id=str(item["id"]),
            name=item.get("name"),
            source="ordinal",
            details={"index": index, "tool": expected_tool},
        )

    def resolve_turn_context(self, message: str) -> TurnContext:
        """Resuelve referencias de forma determinista, sin LLM ni herramientas.

        Nunca ejecuta operaciones: produce únicamente una estructura de datos
        con entidades resueltas, referencias no resueltas y ambiguas, y un
        resumen controlado del estado relevante de esta conversación.
        """
        context = TurnContext()
        context.confirmation = (
            self.session.confirmation_state if self.session.pending_operation else "none"
        )

        # Estado de selección: solo identificadores y nombres, nunca payloads.
        for entity_type in ("fair", "project", "stand", "candidate"):
            selected = self.session.get_selected(entity_type)
            if isinstance(selected, dict) and selected.get("id"):
                context.selected[entity_type] = {
                    "id": str(selected["id"]),
                    "name": selected.get("name"),
                }

        if self.session.last_query_tool:
            context.last_query = {
                "tool": self.session.last_query_tool,
                "summary": self.session.last_query_summary,
                "count": len(self.session.last_query_results or []),
                "fresh": self._last_query_is_fresh(),
            }

        lowered = (message or "").lower()
        entity_types = self._entity_types_in(lowered)
        ordinal_match = _ORDINAL_PATTERN.search(lowered)

        # "stands de esa feria": lista ligada a la feria seleccionada.
        if (
            "stand" in lowered and "feria" in lowered
            and ("esa" in lowered or "la feria" in lowered)
        ):
            fair = self.session.selected_fair
            if (
                isinstance(fair, dict) and fair.get("id")
                and self.session.last_query_tool == "listFairStands"
                and self._last_query_is_fresh()
            ):
                context.resolved.append(ResolvedReference(
                    type="stands_list",
                    id=str(fair["id"]),
                    name=fair.get("name"),
                    source="selected",
                    details={"count": len(self.session.last_query_results or [])},
                ))

        if entity_types:
            for entity_type in entity_types:
                ordinal = (
                    self._resolve_ordinal(lowered, entity_type)
                    if ordinal_match and len(entity_types) == 1 else None
                )
                if ordinal is not None:
                    context.resolved.append(ordinal)
                    continue

                selected = self.session.get_selected(entity_type)
                if isinstance(selected, dict) and selected.get("id"):
                    context.resolved.append(ResolvedReference(
                        type=entity_type,
                        id=str(selected["id"]),
                        name=selected.get("name"),
                        source="selected",
                    ))
                    continue

                candidates = self._recent_candidate_ids(entity_type)
                if len(candidates) == 1:
                    data = next(
                        (
                            item for item in (self.session.last_query_results or [])
                            if isinstance(item, dict) and str(item.get("id")) == candidates[0]
                        ),
                        None,
                    )
                    context.resolved.append(ResolvedReference(
                        type=entity_type,
                        id=candidates[0],
                        name=data.get("name") if data else None,
                        source="mention",
                    ))
                elif candidates:
                    context.ambiguous.append(AmbiguousReference(
                        type=entity_type, candidates=tuple(candidates[:5]),
                    ))
                else:
                    context.unresolved.append(entity_type)
        elif re.search(r"\b(anterior|anteriormente|eso|ello)\b", lowered):
            selected_types = [
                entity_type for entity_type in ("fair", "project", "stand", "candidate")
                if context.selected.get(entity_type, {}).get("id")
            ]
            if len(selected_types) == 1:
                entity_type = selected_types[0]
                item = context.selected[entity_type]
                context.resolved.append(ResolvedReference(
                    type=entity_type, id=item["id"], name=item.get("name"), source="selected",
                ))
            elif selected_types:
                context.ambiguous.append(AmbiguousReference(
                    type="entity",
                    candidates=tuple(context.selected[t]["id"] for t in selected_types),
                    reason="multiple_selected_entities",
                ))
            else:
                recent: list[tuple[str, str]] = []
                for entity_type in ("fair", "project", "stand", "candidate"):
                    recent.extend(
                        (entity_type, entity_id)
                        for entity_id in self._recent_candidate_ids(entity_type)
                    )
                if len(recent) == 1:
                    entity_type, entity_id = recent[0]
                    data = next(
                        (
                            item for item in (self.session.last_query_results or [])
                            if isinstance(item, dict) and str(item.get("id")) == entity_id
                        ),
                        None,
                    )
                    context.resolved.append(ResolvedReference(
                        type=entity_type,
                        id=entity_id,
                        name=data.get("name") if data else None,
                        source="mention",
                    ))
                elif recent:
                    context.ambiguous.append(AmbiguousReference(
                        type="entity",
                        candidates=tuple(entity_id for _, entity_id in recent[:5]),
                        reason="multiple_recent_entities",
                    ))
                else:
                    context.unresolved.append("referencia_anterior")
        elif re.search(r"\b(sus|su)\b", lowered):
            # "sus resultados" no identifica una entidad: no se adivina.
            context.unresolved.append("referencia_contextual")

        seen: set[tuple[str, str]] = set()
        unique: list[ResolvedReference] = []
        for reference in context.resolved:
            key = (reference.type, reference.id)
            if key not in seen:
                seen.add(key)
                unique.append(reference)
        context.resolved = unique
        return context

    def extract_entities_from_message(self, message: str) -> dict[str, str]:
        """Extrae IDs de entidades mencionados explícitamente en el mensaje."""
        entities = {}
        # Patrones como "feria f-123", "proyecto p-456", etc.
        for entity_type, id_field in _ENTITY_KEYWORDS.items():
            pattern = re.compile(
                rf"{entity_type}\s+([a-z0-9-]+)",
                re.IGNORECASE,
            )
            match = pattern.search(message)
            if match:
                entities[id_field] = match.group(1)
        return entities

    def to_dict(self) -> dict[str, Any]:
        """Serializa para almacenamiento/debug."""
        return {
            "session": {
                "selected_fair": self.session.selected_fair,
                "selected_project": self.session.selected_project,
                "selected_stand": self.session.selected_stand,
                "selected_candidate": self.session.selected_candidate,
                "last_query_tool": self.session.last_query_tool,
                "last_query_summary": self.session.last_query_summary,
                "last_query_results": self.session.last_query_results,
                "last_query_turn": self.session.last_query_turn,
                "pending_operation": self.session.pending_operation,
                "confirmation_state": self.session.confirmation_state,
            },
            "mentions": self.mentions._index,
            "current_turn": self._current_turn,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationMemory":
        """Reconstruye desde diccionario serializado."""
        mem = cls()
        mem._current_turn = data.get("current_turn", 0)
        session_data = data.get("session", {})
        mem.session.selected_fair = session_data.get("selected_fair")
        mem.session.selected_project = session_data.get("selected_project")
        mem.session.selected_stand = session_data.get("selected_stand")
        mem.session.selected_candidate = session_data.get("selected_candidate")
        mem.session.last_query_tool = session_data.get("last_query_tool")
        mem.session.last_query_summary = session_data.get("last_query_summary")
        mem.session.last_query_results = session_data.get("last_query_results")
        mem.session.last_query_turn = session_data.get("last_query_turn")
        mem.session.pending_operation = session_data.get("pending_operation")
        mem.session.confirmation_state = session_data.get("confirmation_state", "none")
        mem.mentions._index = data.get("mentions", {})
        return mem

    @classmethod
    def rebuild_from_history(cls, full_history: list[dict]) -> "ConversationMemory":
        """Reconstruye el estado de memoria a partir del historial completo persistido.

        Procesa cada turno en orden cronológico para reconstruir:
        - SessionContext (entidades seleccionadas, última consulta, operación pendiente)
        - EntityMentionIndex (menciones de entidades por turno)
        """
        mem = cls()

        for msg in full_history:
            role = msg.get("role")
            turn_index = msg.get("turn_index", 0)

            # Actualizar contador de turno al máximo encontrado
            if turn_index > mem._current_turn:
                mem._current_turn = turn_index

            # Restaurar estado de confirmación persistido. Las entradas antiguas
            # no tienen estos campos: en ese caso se conserva el estado previo.
            if "pending_operation" in msg:
                pending = msg.get("pending_operation")
                state = msg.get("confirmation_state")
                mem.session.pending_operation = pending if isinstance(pending, dict) and pending else None
                mem.session.confirmation_state = (
                    state if pending and state in ("none", "awaiting_confirmation") else "none"
                )

            if role == "user":
                # Extraer entidades mencionadas explícitamente en el mensaje del usuario
                entities = mem.extract_entities_from_message(msg.get("content", ""))
                for entity_type, entity_id in entities.items():
                    mem.mentions.record_mention(
                        _normalize_entity_type(entity_type), entity_id, turn_index
                    )

            elif role == "assistant":
                # Procesar tool_calls y tool_results del assistant
                tool_calls = msg.get("tool_calls")
                tool_results = msg.get("tool_results")

                if tool_results:
                    for result in tool_results:
                        tool_name = result.get("tool")
                        if tool_name:
                            mem.mentions.record_from_tool_result(tool_name, result, turn_index)
                            mem.session.update_from_tool_result(tool_name, result)
                            mem.session.last_query_turn = turn_index

                # Detectar selección de entidad en la respuesta del assistant
                mem._detect_entity_selection(msg.get("content", ""))

                # Registrar entidades explícitas en el mensaje del assistant (opcional)
                entities = msg.get("entities")
                if entities:
                    for entity_type, entity_id in entities.items():
                        mem.mentions.record_mention(
                            _normalize_entity_type(entity_type), entity_id, turn_index
                        )

            elif role == "tool":
                # Los resultados de herramienta ya se procesan via tool_results en el assistant
                pass

        return mem