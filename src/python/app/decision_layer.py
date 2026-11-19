"""Decision Layer determinista previo al LLM (Fase 2, Etapa 2.4).

Clasifica el mensaje actual usando exclusivamente código local y el
``TurnContext`` ya reconstruido en la Etapa 2.3. Es una capa de
*identificación*: no llama al LLM, no ejecuta herramientas, no autoriza
escrituras, no crea confirmaciones y no modifica propuestas pendientes.
Node.js continúa siendo la fuente de verdad de operaciones y confirmaciones.

Reglas de diseño:
- Prioridad fija (primera coincidencia gana) para que el resultado sea
  reproducible e independiente del orden de evaluación.
- Conservadurismo: ante ambigüedad o falta de evidencia se marca la necesidad
  de aclaración y se desactivan herramientas; nunca se inventa un identificador.
- Una pregunta explicativa que menciona un verbo operativo no es una solicitud
  de ejecución.
- Las señales contradictorias se envían a ``UNKNOWN``/``AMBIGUOUS_REQUEST``.
- ``needs_llm`` es siempre ``True``: las respuestas directas sin LLM y la
  optimización completa de herramientas pertenecen a la Etapa 2.5+.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from .conversation_memory import TurnContext


class DecisionCategory(str, Enum):
    """Categorías mínimas autorizadas para esta etapa."""

    GENERAL_QUESTION = "GENERAL_QUESTION"
    HISTORY_QUERY = "HISTORY_QUERY"
    CONTEXTUAL_QUERY = "CONTEXTUAL_QUERY"
    BUSINESS_QUERY = "BUSINESS_QUERY"
    OPERATION_REQUEST = "OPERATION_REQUEST"
    AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"
    UNKNOWN = "UNKNOWN"


# Motivos cerrados: facilitan las pruebas y la trazabilidad sin texto libre.
DECISION_REASONS = frozenset({
    "general_utterance",
    "history_marker",
    "history_unavailable",
    "ambiguous_reference",
    "unresolved_reference",
    "no_reference_evidence",
    "ambiguous_operation_target",
    "operation_verb_request",
    "explanatory_operation_question",
    "explanatory_general_question",
    "resolved_reference",
    "business_entity",
    "contradictory_operations",
    "contradictory_signals",
    "no_signal",
})


@dataclass(frozen=True)
class Decision:
    """Salida tipada y acotada del Decision Layer."""

    category: DecisionCategory
    reason: str
    needs_llm: bool = True
    allow_tools: bool = True
    ambiguous: bool = False
    needs_clarification: bool = False
    resolved_refs: tuple[str, ...] = ()
    selected_types: tuple[str, ...] = ()
    has_fresh_results: bool = False
    pending_confirmation: bool = False
    history_available: bool = True

    def to_dict(self) -> dict[str, object]:
        """Métrica mínima: sin mensaje, IDs sensibles, tokens ni JWT."""
        return {
            "category": self.category.value,
            "reason": self.reason,
            "needs_llm": self.needs_llm,
            "allow_tools": self.allow_tools,
            "ambiguous": self.ambiguous,
            "needs_clarification": self.needs_clarification,
            "has_resolved_references": bool(self.resolved_refs),
            "has_fresh_results": self.has_fresh_results,
            "pending_confirmation": self.pending_confirmation,
            "history_available": self.history_available,
        }


# --- Normalización y señales léxicas (sin LLM) -------------------------------


def _normalize(text: str) -> str:
    value = (text or "").lower()
    value = "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )
    value = re.sub(r"[¿?¡!.,;:()\[\]{}\"'`]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_WORDS_RE = re.compile(r"[a-z0-9]+")

_GENERAL_UTTERANCES = frozenset({
    "hola", "buenos dias", "buenas tardes", "buenas noches", "hey", "hi",
    "hello", "que tal", "como estas", "como esta", "gracias",
    "muchas gracias", "adios", "hasta luego", "vale", "ok", "entendido",
    "quien eres", "que eres", "que puedes hacer", "que sabes hacer",
    "como funciona", "ayuda", "help", "cual es tu nombre", "como te llamas",
})
_GENERAL_PREFIX_RE = re.compile(
    r"^(hola|buenos dias|buenas tardes|buenas noches|hey|hi|hello|que tal|"
    r"como estas|gracias|adios)\b"
)

_HISTORY_RE = re.compile(
    r"\b(que dijimos|que dije|que te dije|que me dijiste|que preguntaste|"
    r"que pregunte|que hablamos|de que hablamos|que mencionamos|que vimos|"
    r"mi ultima pregunta|mi pregunta anterior|lo que dijimos|lo que hablamos|"
    r"antes dijiste|me dijiste|dijiste que|que respuesta me diste|"
    r"que me respondiste|cuando dije|que pedi|te acuerdas|recuerdas|"
    r"que ferias vimos|que proyectos vimos|que stands vimos|"
    r"que resultados vimos|historial|conversacion anterior)\b"
)

_ANAPHORA_STANDALONE_RE = re.compile(
    r"\b(esa|ese|aquella|aquello|dicha|dicho|anterior|anteriormente|"
    r"sus|su|eso|ello)\b"
)
_ANAPHORA_DEMONSTRATIVE_RE = re.compile(
    r"\b(esta|este|estas|estos)\s+(feria|ferias|proyecto|proyectos|stand|"
    r"stands|candidato|candidatos|candidata|candidatas|resultado|resultados|"
    r"categoria|rubrica|criterio|votacion|evaluacion)\b"
)
_ANAPHORA_SELECTED_RE = re.compile(
    r"\b(la|el|los|las)\s+(anterior|seleccionad\w*|ultim\w*)\b"
)

_ENTITY_TYPE_BY_WORD = {
    "feria": "fair", "ferias": "fair",
    "proyecto": "project", "proyectos": "project",
    "stand": "stand", "stands": "stand",
    "candidato": "candidate", "candidatos": "candidate",
    "candidata": "candidate", "candidatas": "candidate",
    "resultado": "results", "resultados": "results",
    "categoria": "category", "categorias": "category",
    "rubrica": "rubric", "rubricas": "rubric",
    "criterio": "criterion", "criterios": "criterion",
    "votacion": "voting", "votaciones": "voting",
    "evaluacion": "evaluation", "evaluaciones": "evaluation",
    "jurado": "jury", "premio": "award", "premios": "award",
}
_ENTITY_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(_ENTITY_TYPE_BY_WORD, key=len, reverse=True)) + r")\b"
)

# Verbos en formas frecuentes del español. Se comparan como palabras completas:
# una coincidencia aislada nunca autoriza una operación por sí sola.
_OP_VERBS = frozenset({
    "crear", "crea", "creas", "crean", "creo", "creare", "crearia",
    "creando", "creado", "creada", "creados", "creadas",
    "editar", "edita", "editas", "editan", "edito", "editare", "editando",
    "editado", "editada",
    "modificar", "modifica", "modificas", "modifican", "modificare",
    "modificando", "modificado", "modificada",
    "actualizar", "actualiza", "actualizas", "actualizan", "actualizare",
    "actualizando", "actualizado", "actualizada",
    "eliminar", "elimina", "eliminas", "eliminan", "elimino", "eliminare",
    "eliminando", "eliminado", "eliminada",
    "borrar", "borra", "borras", "borran", "borro", "borrare", "borrando",
    "borrado", "borrada",
    "cancelar", "cancela", "cancelas", "cancelan", "cancelo", "cancelare",
    "cancelando", "cancelado", "cancelada",
    "cambiar", "cambia", "cambias", "cambian", "cambio", "cambiare",
    "cambiando", "cambiado", "cambiada",
    "activar", "activa", "activan", "activare", "activando", "activado",
    "activada", "desactivar", "desactiva", "desactivan", "desactivare",
    "desactivando", "desactivado", "desactivada",
    "invertir", "invierte", "invierten", "invertire", "invirtiendo",
    "invertido", "invertida",
    "anadir", "anade", "anaden", "anado", "anadire", "anadiendo", "anadido",
    "agregar", "agrega", "agregan", "agrego", "agregare", "agregando",
    "agregado", "agregada",
    "quitar", "quita", "quitan", "quito", "quitare", "quitando", "quitado",
    "inscribir", "inscribe", "inscriben", "inscribire", "inscribiendo",
    "inscrito", "inscrita", "registrar", "registra", "registran", "registro",
    "registrare", "registrando", "registrado", "registrada",
    "publicar", "publica", "publican", "publico", "publicare", "publicando",
    "publicado", "publicada",
    "abrir", "abre", "abren", "abro", "abrire", "abriendo", "abierto",
    "cerrar", "cierra", "cierran", "cierro", "cerrare", "cerrando",
    "cerrado", "cerrada",
    "generar", "genera", "generan", "genero", "generare", "generando",
    "generado", "generada",
    "subir", "sube", "suben", "subo", "subire", "subiendo", "subido",
    "continuar", "continua", "continuan", "continuare", "continuando",
    "confirmar", "confirma", "confirman", "confirmo", "confirmare",
    "confirmando", "confirmado", "confirmada",
    "ejecutar", "ejecuta", "ejecutan", "ejecuto", "ejecutare", "ejecutado",
})

_EXPLANATORY_RE = re.compile(
    r"\bcomo (se |puedo |podria |podrias |hago |hacer |creo |crea |edito |"
    r"edita |elimino |elimina |cambio |cambia |configuro |funciona |se hace)\b"
    r"|\bque (es|significa|representa|hace|sirve|diferencia)\b"
    r"|\bpara que (sirve|es)\b"
    r"|\bpuedo\b|\bpodria\b|\bpodrias\b|\bse puede\b|\bes posible\b"
    r"|\bdiferencia entre\b|\bcual es la diferencia\b"
    r"|\btutorial\b|\bdocumentacion\b|\bguia\b|\bejemplo de\b|\bmanual\b"
    r"|\bes seguro\b|\bes reversible\b|\bque pasa si\b"
    r"|\bpor que (se |es |debo |tengo )"
)

_REQUEST_FORM_RE = re.compile(
    r"\b(por favor|necesito|quiero|voy a|me gustaria|hazlo|haz|adelante|"
    r"continua|confirma|confirmo|ejecuta|procede)\b"
)

_CONFLICT_GROUPS = (
    (
        frozenset({"crear", "crea", "creo", "anadir", "anade", "agregar", "agrega"}),
        frozenset({"eliminar", "elimina", "borrar", "borra", "cancelar", "cancela"}),
    ),
    (frozenset({"activar", "activa"}), frozenset({"desactivar", "desactiva"})),
    (frozenset({"abrir", "abre"}), frozenset({"cerrar", "cierra"})),
)


def _is_general(norm: str) -> bool:
    return norm in _GENERAL_UTTERANCES or bool(_GENERAL_PREFIX_RE.match(norm))


def _has_anaphora(norm: str) -> bool:
    return bool(
        _ANAPHORA_STANDALONE_RE.search(norm)
        or _ANAPHORA_DEMONSTRATIVE_RE.search(norm)
        or _ANAPHORA_SELECTED_RE.search(norm)
    )


def _entity_types(norm: str) -> tuple[str, ...]:
    return tuple(sorted({
        _ENTITY_TYPE_BY_WORD[word] for word in _ENTITY_WORD_RE.findall(norm)
    }))


def _has_conflicting_operations(words: set[str]) -> bool:
    return any(words & first and words & second for first, second in _CONFLICT_GROUPS)


# --- Punto de entrada --------------------------------------------------------


def decide(
    message: str,
    turn_context: TurnContext,
    *,
    history_available: bool = True,
) -> Decision:
    """Clasifica un turno de forma determinista, sin efectos secundarios."""
    norm = _normalize(message)
    ordered_words = _WORDS_RE.findall(norm)
    words = set(ordered_words)

    resolved_refs = tuple(
        f"{item.type}:{item.id}" for item in turn_context.resolved
    )
    selected_types = tuple(sorted(turn_context.selected))
    ambiguous_refs = tuple(turn_context.ambiguous)
    unresolved = tuple(turn_context.unresolved)
    has_fresh = bool(turn_context.last_query and turn_context.last_query.get("fresh"))
    pending = turn_context.confirmation != "none"

    entity_types = _entity_types(norm)
    has_business = bool(entity_types) or has_fresh
    has_anaphora = _has_anaphora(norm)
    is_general = _is_general(norm)
    is_history = bool(_HISTORY_RE.search(norm))
    has_op_verb = bool(words & _OP_VERBS)
    has_explanatory = bool(_EXPLANATORY_RE.search(norm))
    has_op_request = has_op_verb and not has_explanatory and (
        bool(_REQUEST_FORM_RE.search(norm))
        or (bool(ordered_words) and ordered_words[0] in _OP_VERBS)
    )
    contradictory_ops = _has_conflicting_operations(words)

    def build(
        category: DecisionCategory,
        reason: str,
        *,
        allow_tools: bool = True,
        ambiguous: bool | None = None,
        clarification: bool = False,
    ) -> Decision:
        return Decision(
            category=category,
            reason=reason,
            needs_llm=True,  # Respuestas directas: Etapa 2.5+ (fuera de alcance).
            allow_tools=allow_tools,
            ambiguous=(
                bool(ambiguous_refs) or category is DecisionCategory.AMBIGUOUS_REQUEST
                if ambiguous is None else ambiguous
            ),
            needs_clarification=clarification,
            resolved_refs=resolved_refs,
            selected_types=selected_types,
            has_fresh_results=has_fresh,
            pending_confirmation=pending,
            history_available=history_available,
        )

    # 1) Señales contradictorias: no se adivina ni se autoriza nada.
    if contradictory_ops:
        return build(
            DecisionCategory.UNKNOWN, "contradictory_operations",
            allow_tools=False, ambiguous=True, clarification=True,
        )
    if is_general and (has_op_request or has_business):
        return build(
            DecisionCategory.UNKNOWN, "contradictory_signals",
            allow_tools=False, ambiguous=True, clarification=True,
        )

    # 2) conversación general: nunca herramientas de negocio por defecto.
    if is_general:
        return build(DecisionCategory.GENERAL_QUESTION, "general_utterance", allow_tools=False)

    # 3) historial: solo se apoya en lo realmente persistido (`messages`).
    if is_history and not has_op_request:
        reason = "history_marker" if history_available else "history_unavailable"
        return build(DecisionCategory.HISTORY_QUERY, reason, allow_tools=False)

    # 4) referencias contextuales sin evidencia suficiente → aclaración.
    if has_anaphora and (ambiguous_refs or unresolved):
        reason = "ambiguous_reference" if ambiguous_refs else "unresolved_reference"
        return build(
            DecisionCategory.AMBIGUOUS_REQUEST, reason,
            allow_tools=False, clarification=True,
        )
    if has_anaphora and not resolved_refs and not selected_types and not has_fresh:
        return build(
            DecisionCategory.AMBIGUOUS_REQUEST, "no_reference_evidence",
            allow_tools=False, clarification=True,
        )

    # 5) consulta contextual sobre una entidad ya resuelta o seleccionada:
    #    solo con anáfora real que apunte a la referencia, no por mención de tipo.
    if (resolved_refs or selected_types) and (has_anaphora or is_history):
        return build(DecisionCategory.CONTEXTUAL_QUERY, "resolved_reference", allow_tools=True)

    # 6) posibles operaciones: solo se identifican; Node valida y confirma.
    if has_op_request:
        if ambiguous_refs or (has_anaphora and unresolved):
            return build(
                DecisionCategory.AMBIGUOUS_REQUEST, "ambiguous_operation_target",
                allow_tools=False, clarification=True,
            )
        return build(DecisionCategory.OPERATION_REQUEST, "operation_verb_request", allow_tools=True)

    # 7) pregunta explicativa con verbo operativo: no es ejecución.
    if has_explanatory and has_op_verb:
        if entity_types:
            return build(
                DecisionCategory.BUSINESS_QUERY, "explanatory_operation_question",
                allow_tools=False,
            )
        return build(
            DecisionCategory.GENERAL_QUESTION, "explanatory_general_question",
            allow_tools=False,
        )

    # 8) consulta de negocio con herramientas disponibles.
    if has_business:
        return build(DecisionCategory.BUSINESS_QUERY, "business_entity", allow_tools=True)

    # 9) sin señales suficientes: política fail-closed → sin herramientas.
    return build(DecisionCategory.UNKNOWN, "no_signal", allow_tools=False)


def decision_metrics(decision: Decision, request_id: str = "-") -> dict[str, object]:
    """Envoltorio de logging estructurado (sin contenido del mensaje)."""
    return {"event": "decision_layer", "request_id": request_id, **decision.to_dict()}
