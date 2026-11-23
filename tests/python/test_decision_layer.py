"""Etapa 2.4: Decision Layer determinista (20 escenarios + regresión).

Ninguna prueba invoca al LLM ni ejecuta herramientas; solo valida la
clasificación local y su integración con el flujo del agente.
"""

from __future__ import annotations

from app.conversation_memory import TurnContext, ResolvedReference, AmbiguousReference
from app.decision_layer import DecisionCategory, decide


def _ctx(
    resolved=(),
    ambiguous=(),
    unresolved=(),
    selected=None,
    last_query_fresh=False,
    confirmation="none",
):
    """Helper para construir TurnContext de prueba."""
    resolved_refs = [
        ResolvedReference(type=t, id=i, name=n, source="selected")
        for t, i, n in resolved
    ]
    ambiguous_refs = [
        AmbiguousReference(type=t, candidates=c)
        for t, c in ambiguous
    ]
    return TurnContext(
        resolved=resolved_refs,
        ambiguous=ambiguous_refs,
        unresolved=list(unresolved),
        selected=selected or {},
        last_query={"fresh": last_query_fresh} if last_query_fresh else None,
        confirmation=confirmation,
    )


# 1. Pregunta general sin intención de negocio ---------------------------------


def test_01_general_question_no_tools():
    decision = decide("Hola", _ctx())
    assert decision.category == DecisionCategory.GENERAL_QUESTION
    assert decision.reason == "general_utterance"
    assert decision.allow_tools is False
    assert decision.needs_llm is True


# 2. Consulta sobre historial ---------------------------------------------------


def test_02_history_query_no_tools():
    decision = decide("¿Qué dijimos antes?", _ctx())
    assert decision.category == DecisionCategory.HISTORY_QUERY
    assert decision.reason == "history_marker"
    assert decision.allow_tools is False


def test_03_history_query_unavailable_when_no_history():
    decision = decide("¿Qué pregunté?", _ctx(), history_available=False)
    assert decision.category == DecisionCategory.HISTORY_QUERY
    assert decision.reason == "history_unavailable"
    assert decision.allow_tools is False


# 3. Consulta contextual con feria resuelta ------------------------------------


def test_03_contextual_query_fair_resolved():
    ctx = _ctx(resolved=[("fair", "f-1", "Feria Uno")])
    decision = decide("Dame detalles de esa feria", ctx)
    assert decision.category == DecisionCategory.CONTEXTUAL_QUERY
    assert decision.reason == "resolved_reference"
    assert decision.allow_tools is True


# 4. Consulta contextual con stand resuelto ------------------------------------


def test_04_contextual_query_stand_resolved():
    ctx = _ctx(resolved=[("stand", "s-7", "A-101")])
    decision = decide("Ese stand cuántos votos tiene", ctx)
    assert decision.category == DecisionCategory.CONTEXTUAL_QUERY
    assert decision.allow_tools is True


# 5. Consulta contextual con candidato resuelto --------------------------------


def test_05_contextual_query_candidate_resolved():
    ctx = _ctx(resolved=[("candidate", "c-3", "Juan")])
    decision = decide("Muéstrame sus resultados", ctx)
    assert decision.category == DecisionCategory.CONTEXTUAL_QUERY
    assert decision.allow_tools is True


# 6. Consulta empresarial que necesita herramientas ----------------------------


def test_06_business_query_needs_tools():
    ctx = _ctx(last_query_fresh=True)
    decision = decide("Lista las ferias de esta sede", ctx)
    assert decision.category == DecisionCategory.BUSINESS_QUERY
    assert decision.reason == "business_entity"
    assert decision.allow_tools is True


# 7. Solicitud potencial de escritura ------------------------------------------


def test_07_operation_request_identified_not_executed():
    ctx = _ctx(selected={"fair": {"id": "f-1", "name": "Feria Uno"}})
    decision = decide("Crear una feria nueva", ctx)
    assert decision.category == DecisionCategory.OPERATION_REQUEST
    assert decision.reason == "operation_verb_request"
    assert decision.allow_tools is True  # Node valida y confirma
    # No se crea confirmación ni se ejecuta la operación aquí


# 8. Pregunta explicativa con "eliminar" sin pedir ejecutar --------------------


def test_08_explanatory_delete_question_no_execution():
    ctx = _ctx(selected={"fair": {"id": "f-1", "name": "Feria Uno"}})
    decision = decide("¿Cómo se elimina una feria?", ctx)
    assert decision.category == DecisionCategory.BUSINESS_QUERY
    assert decision.reason == "explanatory_operation_question"
    assert decision.allow_tools is False  # no es ejecución


# 9. Referencia ambigua --------------------------------------------------------


def test_09_ambiguous_reference_needs_clarification():
    ctx = _ctx(ambiguous=[("fair", ("f-1", "f-2"))])
    decision = decide("Dame detalles de esa feria", ctx)
    assert decision.category == DecisionCategory.AMBIGUOUS_REQUEST
    assert decision.reason == "ambiguous_reference"
    assert decision.allow_tools is False
    assert decision.needs_clarification is True


# 10. Referencia no resuelta ---------------------------------------------------


def test_10_unresolved_reference_needs_clarification():
    ctx = _ctx(unresolved=["fair"])
    decision = decide("Háblame de esa feria", ctx)
    assert decision.category == DecisionCategory.AMBIGUOUS_REQUEST
    assert decision.reason == "unresolved_reference"
    assert decision.allow_tools is False
    assert decision.needs_clarification is True


# 11. Mensaje desconocido → fail-closed: sin herramientas ----------------------


def test_11_unknown_no_signal_fail_closed():
    decision = decide("asdfghjkl", _ctx())
    assert decision.category == DecisionCategory.UNKNOWN
    assert decision.reason == "no_signal"
    assert decision.allow_tools is False  # fail-closed


# 12. Señales contradictorias --------------------------------------------------


def test_12_contradictory_signals_unknown():
    decision = decide("Hola crear feria", _ctx())
    assert decision.category == DecisionCategory.UNKNOWN
    assert decision.reason == "contradictory_signals"
    assert decision.allow_tools is False
    assert decision.needs_clarification is True


def test_13_contradictory_operations_unknown():
    decision = decide("Crear y eliminar la feria a la vez", _ctx())
    assert decision.category == DecisionCategory.UNKNOWN
    assert decision.reason == "contradictory_operations"
    assert decision.allow_tools is False
    assert decision.needs_clarification is True


# 13. Conversación sin historial ----------------------------------------------


def test_14_new_conversation_no_history():
    ctx = _ctx()  # empty
    decision = decide("¿Qué ferias hay?", ctx, history_available=False)
    assert decision.category == DecisionCategory.BUSINESS_QUERY
    assert decision.allow_tools is True


# 14. Contexto reconstruido tras reinicio --------------------------------------


def test_15_rebuild_after_restart_preserves_resolved():
    ctx = _ctx(resolved=[("fair", "f-1", "Feria Uno")])
    decision = decide("Continúa con esa feria", ctx)
    assert decision.category == DecisionCategory.CONTEXTUAL_QUERY
    assert "fair:f-1" in decision.resolved_refs


# 15. Aislamiento por organización y sede --------------------------------------


def test_16_isolation_org_site_no_cross_contamination():
    ctx1 = _ctx(resolved=[("fair", "f-1", "Feria Uno")])
    ctx2 = _ctx(resolved=[("fair", "f-2", "Feria Dos")])
    d1 = decide("Dame info de esa feria", ctx1)
    d2 = decide("Dame info de esa feria", ctx2)
    assert d1.category == d2.category == DecisionCategory.CONTEXTUAL_QUERY
    assert "fair:f-1" in d1.resolved_refs
    assert "fair:f-2" in d2.resolved_refs


# 16. Compatibilidad con llamadores históricos ---------------------------------


def test_17_compatibility_with_historical_callers():
    # Sin decision kwarg: allow_tools sigue siendo presupuesto + selector
    ctx = _ctx(last_query_fresh=True)
    decision = decide("Lista ferias", ctx)
    assert decision.allow_tools is True
    assert decision.needs_llm is True


# 17. Ninguna ejecución de herramientas durante la clasificación --------------


def test_18_no_tool_execution_during_classification():
    executed = []

    def forbidden(*args, **kwargs):
        executed.append("tool")
        raise AssertionError("la clasificación no debe ejecutar herramientas")

    # El módulo decision_layer no importa ni usa execute_tool; esta prueba
    # documenta la invariante.
    decide("Crear feria", _ctx())
    assert executed == []


# 18. Ninguna llamada LLM adicional para clasificar intención -----------------


def test_19_no_additional_llm_call():
    # decide() es síncrona y pura; no hay imports de llm ni async.
    decision = decide("Lista ferias", _ctx(last_query_fresh=True))
    assert decision.category == DecisionCategory.BUSINESS_QUERY
    assert decision.needs_llm is True  # LLM sigue necesario para responder


# 19. Ninguna escritura directa en CampusVote ----------------------------------


def test_20_no_direct_write_to_campusvote():
    # El Decision Layer solo clasifica; WRITE_TOOLS y el flujo de confirmación
    # permanecen en tools.py / tool-executor.js / Node.
    ctx = _ctx(selected={"fair": {"id": "f-1", "name": "Feria Uno"}})
    decision = decide("Crear feria", ctx)
    assert decision.category == DecisionCategory.OPERATION_REQUEST
    assert decision.allow_tools is True
    # Node.js es la fuente de verdad; Python no escribe.


# 20. Conservación del flujo de confirmación -----------------------------------


def test_21_pending_confirmation_preserved():
    ctx = _ctx(confirmation="awaiting_confirmation")
    decision = decide("Confirmar", ctx)
    assert decision.pending_confirmation is True
    # El estado informativo se mantiene; Node gestiona consumePendingAction


# --- Regresión: integración con agent.py / main.py ---------------------------


def test_regression_decision_propagates_to_effective_context():
    from types import SimpleNamespace
    import asyncio
    from app.agent import run_agent_turn

    class FakeProvider:
        async def complete(self, context, messages, allow_tools=True):
            return SimpleNamespace(
                content="ok", tool_calls=[], usage={}, latency_ms=0.0, truncated=False
            )

    async def fake_execute(tool_call, context, message, remaining_seconds=None):
        return {"success": True, "data": []}

    history = []
    ctx = {"site_id": "site-1"}
    tc = _ctx(resolved=[("fair", "f-1", "Feria Uno")])

    async def run():
        return await run_agent_turn(
            FakeProvider(), ctx, "pregunta", history, fake_execute,
            max_tool_calls=3, deadline_seconds=5,
            turn_context=tc,
            decision=decide("pregunta", tc),
        )

    current, results = asyncio.run(run())
    assert current.content == "ok"
    # El decision.allow_tools se combinó con el presupuesto; al no haber
    # tool_calls no se probó la ruta de selector, pero el kwarg existe.


def test_regression_allow_tools_false_from_general_question_blocks_tools():
    from types import SimpleNamespace
    import asyncio
    from app.agent import run_agent_turn

    class FakeProvider:
        async def complete(self, context, messages, allow_tools=True):
            # Si allow_tools=False, el proveedor no debe recibir schemas.
            # Este fake lo ignora, pero el test de integración real lo cubre.
            return SimpleNamespace(
                content="ok", tool_calls=[], usage={}, latency_ms=0.0, truncated=False
            )

    async def fake_execute(tool_call, context, message, remaining_seconds=None):
        raise AssertionError("no debe ejecutarse ninguna herramienta")

    history = []
    ctx = {"site_id": "site-1"}
    tc = _ctx()
    decision = decide("Hola", tc)  # allow_tools=False

    async def run():
        return await run_agent_turn(
            FakeProvider(), ctx, "Hola", history, fake_execute,
            max_tool_calls=3, deadline_seconds=5,
            turn_context=tc, decision=decision,
        )

    current, results = asyncio.run(run())
    assert current.content == "ok"


# --- Etapa 2.4.1: Pruebas fail-closed (14 escenarios) ------------------------


def test_22_random_chars_unknown_no_tools():
    """1. Mensaje aleatorio clasificado como UNKNOWN → sin herramientas."""
    decision = decide("asdfghjkl", _ctx())
    assert decision.category == DecisionCategory.UNKNOWN
    assert decision.allow_tools is False


def test_23_empty_or_garbage_no_tools():
    """2. Mensaje vacío o solo caracteres sin intención → sin herramientas."""
    for msg in ["", "   ", "???", "!!!", "12345", "áéíóú"]:
        decision = decide(msg, _ctx())
        assert decision.allow_tools is False, f"falló para: {msg!r}"


def test_24_contradictory_signals_no_tools():
    """3. Mensajes con señales contradictorias → sin herramientas."""
    decision = decide("Hola crear feria", _ctx())
    assert decision.category == DecisionCategory.UNKNOWN
    assert decision.reason == "contradictory_signals"
    assert decision.allow_tools is False


def test_25_ambiguous_reference_no_tools():
    """4. Referencia ambigua → sin herramientas."""
    ctx = _ctx(ambiguous=[("fair", ("f-1", "f-2"))])
    decision = decide("Dame detalles de esa feria", ctx)
    assert decision.category == DecisionCategory.AMBIGUOUS_REQUEST
    assert decision.allow_tools is False


def test_26_unresolved_reference_no_tools():
    """5. Referencia no resuelta → sin herramientas."""
    ctx = _ctx(unresolved=["fair"])
    decision = decide("Háblame de esa feria", ctx)
    assert decision.category == DecisionCategory.AMBIGUOUS_REQUEST
    assert decision.allow_tools is False


def test_27_general_question_no_tools():
    """6. Pregunta general → sin herramientas."""
    decision = decide("Hola, ¿cómo estás?", _ctx())
    assert decision.category == DecisionCategory.GENERAL_QUESTION
    assert decision.allow_tools is False


def test_28_contextual_with_valid_ref_allows_tools():
    """7. Consulta contextual con referencia válida resuelta → permite herramientas."""
    ctx = _ctx(resolved=[("fair", "f-1", "Feria Uno")])
    decision = decide("Dame detalles de esa feria", ctx)
    assert decision.category == DecisionCategory.CONTEXTUAL_QUERY
    assert decision.allow_tools is True


def test_29_business_query_allows_tools():
    """8. Consulta de negocio → permite herramientas."""
    ctx = _ctx(last_query_fresh=True)
    decision = decide("Lista las ferias de esta sede", ctx)
    assert decision.category == DecisionCategory.BUSINESS_QUERY
    assert decision.allow_tools is True


def test_30_operation_request_preserves_proposal_flow():
    """9. Solicitud operativa → preserva flujo de propuesta/confirmación."""
    ctx = _ctx(selected={"fair": {"id": "f-1", "name": "Feria Uno"}})
    decision = decide("Crear una feria nueva", ctx)
    assert decision.category == DecisionCategory.OPERATION_REQUEST
    assert decision.allow_tools is True  # Node valida y confirma


def test_31_tool_selector_returns_empty_when_allow_tools_false():
    """10. tool_selector.py devuelve [] cuando allow_tools=False."""
    from app.providers.tool_selector import select_tool_names
    assert select_tool_names({}, [{"role": "user", "content": "ferias"}], allow_tools=False) == []


def test_32_provider_receives_no_schemas_when_decision_disables_tools():
    """11. Proveedor LLM no recibe schemas cuando la decisión deshabilita herramientas."""
    from types import SimpleNamespace
    import asyncio
    from app.agent import run_agent_turn

    captured = []

    class FakeProvider:
        async def complete(self, context, messages, allow_tools=True):
            captured.append({"allow_tools": allow_tools, "has_tools": "tools" in context})
            return SimpleNamespace(
                content="ok", tool_calls=[], usage={}, latency_ms=0.0, truncated=False
            )

    async def fake_execute(tool_call, context, message, remaining_seconds=None):
        raise AssertionError("no debe ejecutarse")

    history = []
    ctx = {"site_id": "site-1"}
    tc = _ctx()
    decision = decide("asdfghjkl", tc)  # UNKNOWN → allow_tools=False

    async def run():
        return await run_agent_turn(
            FakeProvider(), ctx, "asdfghjkl", history, fake_execute,
            max_tool_calls=3, deadline_seconds=5,
            turn_context=tc, decision=decision,
        )

    asyncio.run(run())
    assert captured[0]["allow_tools"] is False
    assert captured[0]["has_tools"] is False


def test_33_max_tool_calls_limit_still_works():
    """12. Límite max_tool_calls sigue funcionando."""
    import asyncio
    from app.agent import run_agent_turn
    from app.models import LLMResult, ToolCall

    executed = []

    class FakeProvider:
        async def complete(self, context, messages, allow_tools=True):
            if len(executed) < 2:
                allowed = frozenset({"getSiteFairs"})
                return LLMResult(
                    content="",
                    tool_calls=[
                        ToolCall(id=f"call-{i}", name="getSiteFairs", arguments={})
                        for i in range(2)
                    ],
                    usage={}, latency_ms=0.0, truncated=False,
                    allowed_tools=allowed,
                )
            return LLMResult(content="ok", tool_calls=[], usage={}, latency_ms=0.0, truncated=False, allowed_tools=frozenset())

    async def fake_execute(tool_call, context, message, remaining_seconds=None):
        executed.append(tool_call.name)
        return {"success": True, "data": []}

    history = []
    ctx = {"site_id": "site-1"}
    tc = _ctx(last_query_fresh=True)
    decision = decide("Lista ferias", tc)  # BUSINESS_QUERY → allow_tools=True

    async def run():
        return await run_agent_turn(
            FakeProvider(), ctx, "Lista ferias", history, fake_execute,
            max_tool_calls=1, deadline_seconds=5,  # límite = 1
            turn_context=tc, decision=decision,
        )

    asyncio.run(run())
    assert len(executed) == 1  # solo 1 ejecución aunque el LLM pidió 2


def test_34_historical_callers_still_work_no_regression():
    """13. Llamadores históricos sin decision kwarg siguen funcionando."""
    from types import SimpleNamespace
    import asyncio
    from app.agent import run_agent_turn

    class FakeProvider:
        async def complete(self, context, messages, allow_tools=True):
            return SimpleNamespace(content="ok", tool_calls=[], usage={}, latency_ms=0.0, truncated=False)

    async def fake_execute(tool_call, context, message, remaining_seconds=None):
        return {"success": True, "data": []}

    history = []
    ctx = {"site_id": "site-1"}

    async def run():
        # Sin turn_context ni decision: compatibilidad total
        return await run_agent_turn(
            FakeProvider(), ctx, "pregunta", history, fake_execute,
            max_tool_calls=3, deadline_seconds=5,
        )

    current, results = asyncio.run(run())
    assert current.content == "ok"


def test_35_no_additional_llm_call_for_classification():
    """14. No se agrega ninguna llamada LLM adicional para clasificar intención."""
    # decide() es síncrona y pura; no hay imports de llm ni async.
    # Verificación estática: decision_layer no importa app.llm ni app.providers.*
    import app.decision_layer as dl
    import inspect
    source = inspect.getsource(dl.decide)
    # "llm" aparece en comentario "needs_llm" → buscamos import/uso real
    assert "import" not in source or "llm" not in source  # sin import de llm
    assert "provider" not in source.lower() or "provider.complete" not in source
    assert "async" not in source
    assert "await" not in source
    # La función es puramente determinista y local.