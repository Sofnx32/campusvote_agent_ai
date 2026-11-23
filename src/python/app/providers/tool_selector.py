"""Selección determinista de herramientas anunciadas al proveedor (Fase 2).

El registro de herramientas, sus permisos y la validación global del parser no
se modifican: este módulo solo decide qué subconjunto de ``TOOLS`` se anuncia
en ``payload["tools"]``. El default es el registro completo, por lo que
cualquier señal ambigua conserva las 23 herramientas.
"""

from __future__ import annotations

import re
import unicodedata

from .tool_registry import TOOLS

# Dominios derivados del registro y del prompt existente. Las escrituras
# incluyen siempre sus lecturas prerrequisito para no romper secuencias.
_DOMAIN_RULES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (
        ("feria", "ferias", "stand", "rúbrica", "rubrica", "categoría", "categoria"),
        frozenset({
            "getSiteFairs", "getSiteOverview", "listFairCategories",
            "listFairCategoryRubrics", "listFairStands",
            "createFair", "updateFair", "changeFairStatus",
            "createFairRubric", "addFairRubricCriterion",
            "createCategoryFairRubric", "addCategoryFairRubricCriterion",
            "createFairStand", "updateFairStand", "deleteFairStand",
        }),
    ),
    (
        ("proyecto", "proyectos", "resultado", "resultados", "ranking", "estadística", "estadistica"),
        frozenset({"getSiteProjects", "getSiteResults", "getSiteFairs"}),
    ),
    (
        ("criterio", "criterios", "docente", "evaluación", "evaluacion", "rúbrica", "rubrica"),
        frozenset({
            "listTeachingCriteria", "listFairCategories", "listFairCategoryRubrics",
            "createTeachingCriterion", "updateTeachingCriterion",
            "toggleTeachingCriterion", "deleteTeachingCriterion",
            "createFairRubric", "addFairRubricCriterion",
            "createCategoryFairRubric", "addCategoryFairRubricCriterion",
        }),
    ),
)

# Ventana de historial usada SOLO para detectar operaciones en curso y
# herramientas ya invocadas; nunca para convertir el mensaje actual en general.
_HISTORY_LOOKBACK = 6

# Criterio positivo y cerrado: el mensaje actual se omite únicamente si está
# formado íntegramente por estas expresiones conversacionales simples. No es
# una lista de exclusiones; cualquier palabra ajena devuelve las 23 herramientas.
_GENERAL_UTTERANCES: frozenset[str] = frozenset({
    "hola", "hey", "buenos dias", "buenas tardes", "buenas noches",
    "gracias", "muchas gracias", "adios", "hasta luego",
    "como estas", "quien eres", "que puedes hacer", "ayuda",
    "buena idea", "de acuerdo", "entendido", "vale", "ok", "okay",
})
_GENERAL_VOCAB: frozenset[str] = frozenset(
    word for phrase in _GENERAL_UTTERANCES for word in phrase.split()
)
_GENERAL_MAX_WORDS = 4

# Respuestas afirmativas cortas que indican confirmación a una oferta previa.
_AFFIRMATIVE_UTTERANCES: frozenset[str] = frozenset({
    "si", "sí", "si por favor", "sí por favor",
    "claro", "claro que si", "claro que sí",
    "dale", "dale pues",
    "ok", "okey", "okey dokey",
    "por supuesto", "por supesto",
    "de acuerdo", "deacuerdo",
    "adelante", "adelante pues",
    "correcto", "exacto", "exactamente",
    "esta bien", "está bien", "esta bien por favor", "está bien por favor",
    "esa misma", "esa mismo",
    "perfecto", "bien", "bien pues",
    "va", "vamos", "vamos pues",
    "sip", "si po", "si po favor",
})


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _normalize_utterance(message: str) -> str:
    """Minúsculas, sin diacríticos ni puntuación; palabras separadas por spaces."""
    text = _strip_accents((message or "").lower())
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _is_plain_greeting(utterance: str) -> bool:
    """True solo si TODO el mensaje es una expresión conversacional reconocida."""
    if utterance in _GENERAL_UTTERANCES:
        return True
    words = utterance.split()
    if not words or len(words) > _GENERAL_MAX_WORDS:
        return False
    return all(word in _GENERAL_VOCAB for word in words)


def _is_affirmative(utterance: str) -> bool:
    return utterance in _AFFIRMATIVE_UTTERANCES


def _has_selected_fair(context: dict) -> bool:
    """Verifica si hay una feria seleccionada en el contexto de conversación."""
    conv_ctx = context.get("conversation_context") or context.get("turn_context") or {}
    selected = conv_ctx.get("selected") if isinstance(conv_ctx, dict) else {}
    return isinstance(selected, dict) and "fair" in selected and isinstance(selected["fair"], dict) and selected["fair"].get("id")


def _text(messages: list[dict]) -> str:
    """Concatena contenido de mensajes recientes para detección de dominio."""
    parts: list[str] = []
    for message in messages[-_HISTORY_LOOKBACK:]:
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(content.lower())
    return " ".join(parts)


def _history_tool_names(messages: list[dict]) -> set[str]:
    """Nombres de herramientas ya invocadas en el historial cercano."""
    names: set[str] = set()
    for message in messages[-_HISTORY_LOOKBACK:]:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = function.get("name")
            if isinstance(name, str) and name in TOOLS:
                names.add(name)
    return names


def _history_has_pending_context(messages: list[dict]) -> bool:
    """Cualquier contenido sustantivo reciente deja contexto operativo pendiente.

    Criterio positivo (no lista de exclusiones): solo se considera conversacional
    un historial formado íntegramente por expresiones simples reconocidas. Un
    mensaje sustantivo (consulta, identificador, token, UUID, dominio, respuesta
    del asistente) o cualquier ``tool_calls`` impide devolver 0 herramientas.
    """
    for message in messages[-_HISTORY_LOOKBACK:]:
        if message.get("tool_calls"):
            return True
        if message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if not _is_plain_greeting(_normalize_utterance(content)):
            return True
    return False


def is_general_conversation(message: str, messages: list[dict]) -> bool:
    """True solo para conversación general inequívoca, sin operación pendiente.

    El criterio se aplica al mensaje actual completo (frase reconocida o
    combinación cerrada de palabras conversacionales). El historial puede
    impedir la clasificación por operación en curso o herramientas ya usadas,
    pero nunca puede convertir por sí solo una petición en conversación general.
    """
    utterance = _normalize_utterance(message)
    if not utterance or not _is_plain_greeting(utterance):
        return False
    if _history_tool_names(messages):
        return False
    if _history_has_pending_context(messages):
        return False
    return True


def select_tool_names(
    context: dict,
    messages: list[dict],
    allow_tools: bool = True,
) -> list[str]:
    """Devuelve los nombres a anunciar, en orden canónico del registro.

    - ``allow_tools=False`` → lista vacía (el proveedor no envía ``tools``).
    - Conversación general inequívoca → lista vacía (respuesta directa).
    - Dominio reconocido → unión de ese dominio y herramientas del historial.
    - Respuesta afirmativa con feria seleccionada → asegurar ``listFairCategories``.
    - Cualquier otra situación → las 23 herramientas del registro.
    """
    if not allow_tools:
        return []
    message = ""
    for candidate in reversed(messages):
        if candidate.get("role") == "user":
            content = candidate.get("content")
            if isinstance(content, str):
                message = content
                break
    if is_general_conversation(message, messages):
        return []
    combined = _normalize_utterance(_text(messages) + " " + (message or ""))
    matched = False
    selected: set[str] = set()
    for keywords, names in _DOMAIN_RULES:
        for keyword in keywords:
            needle = _strip_accents(keyword.lower())
            if re.search(r"\b" + re.escape(needle) + r"\b", combined):
                matched = True
                selected |= set(names)
                break
    # Si el usuario responde afirmativamente y hay una feria seleccionada,
    # asegurar que listFairCategories esté disponible para listar categorías.
    if not matched and _is_affirmative(_normalize_utterance(message)) and _has_selected_fair(context):
        matched = True
        selected.add("listFairCategories")
    # Sin dominio reconocido, la ambigüedad conserva las 23 herramientas;
    # el historial solo afina cuando además hay una intención de dominio clara.
    if not matched:
        return list(TOOLS)
    selected |= _history_tool_names(messages)
    # Orden canónico del registro; nunca se agrega ni se elimina del universo.
    return [name for name in TOOLS if name in selected]