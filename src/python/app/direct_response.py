"""Direct Response Layer (Fase 2, Etapa 2.5).

Capa determinista que resuelve consultas simples usando solo datos
confiables de la conversación (memoria, historial, TurnContext, Decision)
SIN invocar al LLM ni ejecutar herramientas.

Reglas de seguridad (fail-safe):
- Solo datos de la conversación autenticada.
- Nunca inventar nombres, IDs, estados, cantidades ni resultados.
- Resultados obsoletos (last_query.fresh=False) → NOT_ANSWERABLE.
- Preguntas que requieren datos actuales → NOT_ANSWERABLE.
- Escrituras/confirmaciones → NOT_ANSWERABLE (Node es la autoridad).
- Ante cualquier duda → NOT_ANSWERABLE / AMBIGUOUS (fallback al agente).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .conversation_memory import ConversationMemory, TurnContext, ResolvedReference
from .decision_layer import Decision, DecisionCategory
from .forensic import log_direct_response_evaluated, log_direct_response_used


class DirectResponseState(str, Enum):
    """Estados posibles de la respuesta directa."""

    ANSWERED = "ANSWERED"           # Respondido directamente con evidencia verificable
    NOT_ANSWERABLE = "NOT_ANSWERABLE"  # Falta evidencia o requiere datos actuales
    AMBIGUOUS = "AMBIGUOUS"         # Múltiples interpretaciones posibles


@dataclass(frozen=True)
class DirectResponse:
    """Salida tipada e inmutable de la capa de respuesta directa."""

    state: DirectResponseState
    text: str
    reason: str           # Motivo cerrado para trazabilidad
    source: str           # Fuente de información usada

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "text": self.text,
            "reason": self.reason,
            "source": self.source,
        }


# --- Patrones y utilidades ----------------------------------------------------


# Saludos/despedidas/agradecimientos inequívocos (solo estos, sin añadidos)
# Se comparan como frases completas normalizadas (sin acentos, minúsculas, sin puntuación)
_GREETING_PATTERNS = frozenset({
    "hola", "buenos dias", "buenas tardes", "buenas noches", "hey", "hi", "hello", "que tal", "como estas",
})
_FAREWELL_PATTERNS = frozenset({
    "adios", "hasta luego", "chao", "bye", "nos vemos",
})
_THANKS_PATTERNS = frozenset({
    "gracias", "muchas gracias", "thanks", "thank you",
})
_ACK_PATTERNS = frozenset({
    "vale", "ok", "entendido", "de acuerdo", "perfecto", "bien", "correcto",
})


# Preguntas sobre historial/contexto (requieren evidencia en memoria)
# Usan texto normalizado (sin acentos) para matching
_HISTORY_QUESTION_PATTERNS = [
    re.compile(r"\bque\s+(dijimos|dije|te dije|me dijiste|preguntaste|pregunte|hablamos|mencionamos|vimos)\b"),
    re.compile(r"\b(mi ultima pregunta|mi pregunta anterior|lo que dijimos|lo que hablamos)\b"),
    re.compile(r"\b(antes dijiste|me dijiste|dijiste que|que respuesta me diste|que me respondiste)\b"),
    re.compile(r"\b(cuando dije|que pedi|te acuerdas|recuerdas)\b"),
    re.compile(r"\bque\s+(ferias|proyectos|stands|candidatos|resultados)\s+(vimos|habia|había|habian|habían)\b"),
    re.compile(r"\b(que|que tal)\s+(vimos|revisamos|miramos)\b"),
]

# Preguntas sobre entidad seleccionada/resuelta (más flexibles)
# Usan texto normalizado
_CONTEXT_QUESTION_PATTERNS = [
    re.compile(r"\b(cual|que)\s+(es|era|fue)\s+(la|el|los|las)\s+(feria|proyecto|stand|candidato|candidata|resultado)\b"),
    re.compile(r"\b(la|el|los|las)\s+(feria|proyecto|stand|candidato|candidata|resultado)\s+(seleccionada|seleccionado|anterior|ultima|ultimo)\b"),
    re.compile(r"\b(feria|proyecto|stand|candidato|candidata)\s+(seleccionada|seleccionado|anterior)\b"),
    re.compile(r"\b(cual|que)\s+(feria|proyecto|stand|candidato|candidata)\s+(tengo|tenia|teniamos|hay)\b"),
    re.compile(r"\bnombre\s+(de\s+)?(la|el)\s+(feria|proyecto|stand|candidato|candidata)\b"),
    re.compile(r"\b(que|qual|cual)\s+(feria|proyecto|stand|candidato|candidata)\b"),
    re.compile(r"\b(quien|quien es)\s+(el|la)\s+(candidato|candidata)\b"),
]

_CONFIRMATION_QUESTION_PATTERNS = [
    re.compile(r"\b(que pasa|que hay|estado)\s+(con|de)\s+(la|el)\s+(confirmacion|operacion|propuesta)\b", re.IGNORECASE),
    re.compile(r"\b(confirmacion|operacion|propuesta)\s+pendiente\b", re.IGNORECASE),
    re.compile(r"\bhay\s+(confirmacion|operacion|propuesta)\s+pendiente\b", re.IGNORECASE),
]


def _normalize(text: str) -> str:
    """Normaliza para comparación: minúsculas, sin acentos, sin puntuación extra."""
    value = (text or "").lower()
    value = "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn" and (char.isalnum() or char.isspace())
    )
    return re.sub(r"\s+", " ", value).strip()


def _is_pure_greeting(message: str) -> bool:
    """True solo si el mensaje COMPLETO normalizado coincide con un patrón permitido."""
    norm = _normalize(message)
    if not norm:
        return False
    # Comprobar si el mensaje normalizado completo está en los conjuntos permitidos
    allowed = _GREETING_PATTERNS | _FAREWELL_PATTERNS | _THANKS_PATTERNS | _ACK_PATTERNS
    return norm in allowed


def _greeting_response(message: str) -> str:
    """Respuesta determinista para saludos/despedidas/agradecimientos."""
    norm = _normalize(message)
    if norm in _GREETING_PATTERNS:
        return "¡Hola! ¿En qué puedo ayudarte?"
    if norm in _FAREWELL_PATTERNS:
        return "¡Hasta luego! Que tengas un buen día."
    if norm in _THANKS_PATTERNS:
        return "¡De nada! Estoy aquí para lo que necesites."
    if norm in _ACK_PATTERNS:
        return "Entendido. ¿Algo más en lo que pueda ayudarte?"
    return "¡Hola! ¿En qué puedo ayudarte?"


def _match_any(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _get_selected_entity_name(turn_context: TurnContext, entity_type: str) -> str | None:
    """Obtiene el nombre de una entidad seleccionada si existe."""
    selected = turn_context.selected.get(entity_type)
    if isinstance(selected, dict):
        return selected.get("name")
    return None


def _get_resolved_entity_name(turn_context: TurnContext, entity_type: str) -> str | None:
    """Obtiene el nombre de una entidad resuelta en este turno si existe y es única."""
    resolved = [r for r in turn_context.resolved if r.type == entity_type]
    if len(resolved) == 1 and resolved[0].name:
        return resolved[0].name
    return None


def _get_last_query_summary(turn_context: TurnContext) -> str | None:
    """Resumen de la última consulta si es fresca."""
    if turn_context.last_query and turn_context.last_query.get("fresh"):
        tool = turn_context.last_query.get("tool")
        count = turn_context.last_query.get("count", 0)
        summary = turn_context.last_query.get("summary")
        if summary:
            return f"{summary} ({count} resultados)"
        if tool:
            return f"Consulta: {tool} ({count} resultados)"
    return None


def _history_context_response(turn_context: TurnContext, memory: ConversationMemory | None) -> str | None:
    """Construye respuesta sobre qué se estaba revisando/hablando."""
    parts = []

    # Entidad seleccionada actual
    for entity_type in ("fair", "project", "stand", "candidate"):
        name = _get_selected_entity_name(turn_context, entity_type)
        if name:
            type_names = {"fair": "feria", "project": "proyecto", "stand": "stand", "candidate": "candidato/a"}
            parts.append(f"Tenías seleccionada la {type_names[entity_type]} «{name}».")

    # Última consulta fresca
    summary = _get_last_query_summary(turn_context)
    if summary:
        parts.append(f"La última consulta fue: {summary}.")

    # Si hay memoria, verificar último resultado de herramienta con datos
    if memory and memory.session.last_query_results and memory.session.last_query_tool:
        if turn_context.last_query and turn_context.last_query.get("fresh"):
            results = memory.session.last_query_results
            if results:
                tool = memory.session.last_query_tool
                type_map = {
                    "getSiteFairs": "ferias",
                    "getSiteProjects": "proyectos",
                    "listFairStands": "stands",
                    "getSiteResults": "resultados",
                    "listTeachingCriteria": "criterios",
                    "listFairCategories": "categorías",
                    "listFairCategoryRubrics": "rúbricas",
                }
                entity_name = type_map.get(tool, "elementos")
                count = len(results)
                if count == 1:
                    item = results[0]
                    if isinstance(item, dict) and item.get("name"):
                        parts.append(f"Había {count} {entity_name}: «{item['name']}».")
                    else:
                        parts.append(f"Había {count} {entity_name}.")
                elif count > 1:
                    names = [str(r.get("name", r.get("id", "?"))) for r in results[:5] if isinstance(r, dict)]
                    if names:
                        parts.append(f"Había {count} {entity_name}: {', '.join(names)}.")
                    else:
                        parts.append(f"Había {count} {entity_name}.")

    if not parts:
        return None
    return " ".join(parts)


def _context_entity_response(turn_context: TurnContext, entity_type: str) -> str | None:
    """Respuesta sobre entidad seleccionada/resuelta (feria, proyecto, stand, candidato)."""
    # Prioridad: seleccionada > resuelta en este turno
    name = _get_selected_entity_name(turn_context, entity_type)
    source = "selected"
    if not name:
        name = _get_resolved_entity_name(turn_context, entity_type)
        source = "resolved"

    if not name:
        return None

    type_names = {"fair": "feria", "project": "proyecto", "stand": "stand", "candidate": "candidato/a"}
    label = type_names.get(entity_type, entity_type)

    if source == "selected":
        return f"La {label} seleccionada es «{name}»."
    else:
        return f"La {label} a la que te refieres es «{name}»."


def _confirmation_status_response(turn_context: TurnContext) -> str | None:
    """Respuesta informativa sobre confirmación pendiente."""
    if turn_context.confirmation == "awaiting_confirmation":
        return "Hay una operación pendiente de confirmación. Revisa los datos y confirma para aplicarla."
    return None


# --- Punto de entrada principal ----------------------------------------------


def direct_response(
    message: str,
    decision: Decision,
    turn_context: TurnContext,
    memory: ConversationMemory | None,
    history: list[dict],
) -> DirectResponse | None:
    """
    Determina si el mensaje puede responderse directamente con evidencia verificable.

    Retorna DirectResponse si ANSWERED, None si debe delegar al agente.
    NUNCA invoca LLM, herramientas, Redis ni muta estado.
    """
    norm = _normalize(message)

    # 1. Saludos/despedidas/agradecimientos/acks puros → ANSWERED
    if _is_pure_greeting(message):
        # Solo si la decisión coincide (GENERAL_QUESTION sin herramientas)
        if decision.category == DecisionCategory.GENERAL_QUESTION and not decision.allow_tools:
            resp = DirectResponse(
                state=DirectResponseState.ANSWERED,
                text=_greeting_response(message),
                reason="pure_greeting",
                source="deterministic_pattern",
            )
            log_direct_response_evaluated(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
                llm_skipped=True,
                tool_execution_skipped=True,
            )
            log_direct_response_used(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
            )
            return resp
        # Si tiene allow_tools o no es GENERAL_QUESTION, delegar (puede ser "hola, crea feria")

    # 2. Preguntas sobre historial/contexto general → ANSWERED si hay evidencia
    if _match_any(_HISTORY_QUESTION_PATTERNS, norm):
        response = _history_context_response(turn_context, memory)
        if response:
            resp = DirectResponse(
                state=DirectResponseState.ANSWERED,
                text=response,
                reason="history_context_available",
                source="turn_context_and_memory",
            )
            log_direct_response_evaluated(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
                llm_skipped=True,
                tool_execution_skipped=True,
            )
            log_direct_response_used(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
            )
            return resp
        resp = DirectResponse(
            state=DirectResponseState.NOT_ANSWERABLE,
            text="",
            reason="history_context_insufficient",
            source="turn_context_and_memory",
        )
        log_direct_response_evaluated(
            direct_response_state=resp.state.value,
            direct_response_reason=resp.reason,
            direct_response_source=resp.source,
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return resp

    # 3. Preguntas sobre entidad seleccionada/resuelta específica → ANSWERED si hay nombre
    if _match_any(_CONTEXT_QUESTION_PATTERNS, norm):
        # Detectar tipo de entidad en la pregunta
        entity_type = None
        if "feria" in norm:
            entity_type = "fair"
        elif "proyecto" in norm:
            entity_type = "project"
        elif "stand" in norm:
            entity_type = "stand"
        elif "candidato" in norm or "candidata" in norm:
            entity_type = "candidate"

        if entity_type:
            response = _context_entity_response(turn_context, entity_type)
            if response:
                resp = DirectResponse(
                    state=DirectResponseState.ANSWERED,
                    text=response,
                    reason="selected_entity_resolved",
                    source="turn_context_selected_or_resolved",
                )
                log_direct_response_evaluated(
                    direct_response_state=resp.state.value,
                    direct_response_reason=resp.reason,
                    direct_response_source=resp.source,
                    llm_skipped=True,
                    tool_execution_skipped=True,
                )
                log_direct_response_used(
                    direct_response_state=resp.state.value,
                    direct_response_reason=resp.reason,
                    direct_response_source=resp.source,
                )
                return resp
            # Si hay ID pero no nombre → NO inventar
            selected = turn_context.selected.get(entity_type)
            resolved = [r for r in turn_context.resolved if r.type == entity_type]
            if (selected and selected.get("id")) or (resolved and resolved[0].id):
                resp = DirectResponse(
                    state=DirectResponseState.NOT_ANSWERABLE,
                    text="",
                    reason="entity_id_only_no_descriptive_data",
                    source="turn_context_selected_or_resolved",
                )
                log_direct_response_evaluated(
                    direct_response_state=resp.state.value,
                    direct_response_reason=resp.reason,
                    direct_response_source=resp.source,
                    llm_skipped=False,
                    tool_execution_skipped=False,
                )
                return resp
        resp = DirectResponse(
            state=DirectResponseState.NOT_ANSWERABLE,
            text="",
            reason="context_entity_insufficient",
            source="turn_context",
        )
        log_direct_response_evaluated(
            direct_response_state=resp.state.value,
            direct_response_reason=resp.reason,
            direct_response_source=resp.source,
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return resp

    # 4. Preguntas sobre confirmación pendiente → ANSWERED (informativo)
    if _match_any(_CONFIRMATION_QUESTION_PATTERNS, norm):
        response = _confirmation_status_response(turn_context)
        if response:
            resp = DirectResponse(
                state=DirectResponseState.ANSWERED,
                text=response,
                reason="confirmation_status_informational",
                source="turn_context_confirmation",
            )
            log_direct_response_evaluated(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
                llm_skipped=True,
                tool_execution_skipped=True,
            )
            log_direct_response_used(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
            )
            return resp
        resp = DirectResponse(
            state=DirectResponseState.NOT_ANSWERABLE,
            text="",
            reason="no_pending_confirmation",
            source="turn_context_confirmation",
        )
        log_direct_response_evaluated(
            direct_response_state=resp.state.value,
            direct_response_reason=resp.reason,
            direct_response_source=resp.source,
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return resp

    # 5. Referencia ambigua en TurnContext → AMBIGUOUS
    if turn_context.ambiguous:
        resp = DirectResponse(
            state=DirectResponseState.AMBIGUOUS,
            text="",
            reason="ambiguous_reference_in_context",
            source="turn_context_ambiguous",
        )
        log_direct_response_evaluated(
            direct_response_state=resp.state.value,
            direct_response_reason=resp.reason,
            direct_response_source=resp.source,
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return resp

    # 6. Referencias no resueltas en TurnContext → NOT_ANSWERABLE
    if turn_context.unresolved:
        resp = DirectResponse(
            state=DirectResponseState.NOT_ANSWERABLE,
            text="",
            reason="unresolved_reference_in_context",
            source="turn_context_unresolved",
        )
        log_direct_response_evaluated(
            direct_response_state=resp.state.value,
            direct_response_reason=resp.reason,
            direct_response_source=resp.source,
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return resp

    # 7. Resultado obsoleto (fresh=False) consultado como si fuera actual → NOT_ANSWERABLE
    if turn_context.last_query and not turn_context.last_query.get("fresh"):
        # Si la pregunta parece pedir datos actuales
        if any(w in norm for w in ("actual", "ahora", "hoy", "estado", "como esta", "como estan")):
            resp = DirectResponse(
                state=DirectResponseState.NOT_ANSWERABLE,
                text="",
                reason="stale_results_require_fresh_data",
                source="turn_context_last_query",
            )
            log_direct_response_evaluated(
                direct_response_state=resp.state.value,
                direct_response_reason=resp.reason,
                direct_response_source=resp.source,
                llm_skipped=False,
                tool_execution_skipped=False,
            )
            return resp

    # 8. Operaciones de escritura, búsquedas de negocio, datos actuales → delegar
    if decision.category in (
        DecisionCategory.BUSINESS_QUERY,
        DecisionCategory.OPERATION_REQUEST,
    ):
        resp = None
        log_direct_response_evaluated(
            direct_response_state=None,
            direct_response_reason="delegated_to_agent",
            direct_response_source="decision_layer",
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return None

    # 9. AMBIGUOUS_REQUEST o UNKNOWN con allow_tools=False (fail-closed) → delegar al agente
    if decision.category in (
        DecisionCategory.AMBIGUOUS_REQUEST,
        DecisionCategory.UNKNOWN,
    ):
        log_direct_response_evaluated(
            direct_response_state=None,
            direct_response_reason="delegated_to_agent",
            direct_response_source="decision_layer",
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return None

    # 10. HISTORY_QUERY sin evidencia ya capturado arriba; GENERAL_QUESTION sin ser saludo puro
    if decision.category == DecisionCategory.HISTORY_QUERY:
        log_direct_response_evaluated(
            direct_response_state=None,
            direct_response_reason="delegated_to_agent",
            direct_response_source="decision_layer",
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return None

    if decision.category == DecisionCategory.GENERAL_QUESTION:
        # Preguntas de conocimiento general que no son saludos puros → delegar
        log_direct_response_evaluated(
            direct_response_state=None,
            direct_response_reason="delegated_to_agent",
            direct_response_source="decision_layer",
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return None

    # 11. CONTEXTUAL_QUERY sin entidad resuelta/seleccionada con nombre → NOT_ANSWERABLE
    if decision.category == DecisionCategory.CONTEXTUAL_QUERY:
        resp = DirectResponse(
            state=DirectResponseState.NOT_ANSWERABLE,
            text="",
            reason="contextual_query_insufficient_evidence",
            source="turn_context",
        )
        log_direct_response_evaluated(
            direct_response_state=resp.state.value,
            direct_response_reason=resp.reason,
            direct_response_source=resp.source,
            llm_skipped=False,
            tool_execution_skipped=False,
        )
        return resp

    # Por defecto: delegar al agente
    log_direct_response_evaluated(
        direct_response_state=None,
        direct_response_reason="delegated_to_agent",
        direct_response_source="decision_layer",
        llm_skipped=False,
        tool_execution_skipped=False,
    )
    return None


def direct_response_metrics(direct: DirectResponse | None, request_id: str = "-") -> dict[str, Any]:
    """Métrica estructurada para observabilidad (sin mensajes completos, tokens, PII)."""
    if direct is None:
        return {
            "event": "direct_response",
            "request_id": request_id,
            "used": False,
        }
    return {
        "event": "direct_response",
        "request_id": request_id,
        "used": True,
        "state": direct.state.value,
        "reason": direct.reason,
        "source": direct.source,
        "llm_skipped": direct.state == DirectResponseState.ANSWERED,
        "tool_execution_skipped": direct.state == DirectResponseState.ANSWERED,
    }