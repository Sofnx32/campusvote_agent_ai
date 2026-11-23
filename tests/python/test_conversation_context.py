"""Etapa 2.3: integración del Context Manager y resolución segura.

Cubre la resolución determinista de referencias antes de la decisión del
agente, sin LLM adicionales ni ejecución de herramientas durante la
resolución. Redis se usa solo en la instancia de pruebas aislada configurada
por REDIS_URL (por defecto, el contenedor local de tests).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

os.environ.setdefault("AI_SERVICE_TOKEN", "x" * 32)
os.environ.setdefault("NODE_ENV", "development")
os.environ.setdefault("ALLOW_IN_MEMORY_REPLAY", "true")

from app import main  # noqa: E402
from app.conversation_memory import ConversationMemory, TurnContext  # noqa: E402
from app.conversation_store import ConversationStore  # noqa: E402
from app.models import ChatRequest  # noqa: E402


def _context(sub: str = "u1", organization_id: str = "org1", site_id: str = "site1") -> dict:
    return {
        "sub": sub,
        "organization_id": organization_id,
        "site_id": site_id,
        "correlation_id": "corr-ctx",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=25)).isoformat(),
    }


def _memory_with_fairs(*ids: str, turn: int = 2) -> ConversationMemory:
    memory = ConversationMemory()
    memory._current_turn = turn
    fairs = [{"id": value, "name": f"Feria {value}", "status": "OPEN"} for value in ids]
    memory.session.last_query_tool = "getSiteFairs"
    memory.session.last_query_results = fairs
    memory.session.last_query_summary = f"{len(fairs)} ferias"
    memory.session.last_query_turn = turn
    for fair in fairs:
        memory.mentions.record_mention("fair", fair["id"], turn)
    return memory


@pytest_asyncio.fixture
async def redis_store():
    """Fixture que crea un store Redis real. Se salta si Redis no está disponible."""
    import pytest
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        store = await ConversationStore.create(
            redis_url=redis_url,
            max_messages=20,
            ttl_seconds=1800,
        )
        # Verificar que la conexión funciona realmente
        await store.redis.ping()
    except Exception as e:
        pytest.skip(f"Redis no disponible en {redis_url}: {e}")
    
    yield store
    
    if store.redis:
        keys = await store.redis.keys("ai:conversation:test:context:*")
        if keys:
            await store.redis.delete(*keys)
        await store.redis.close()


# 1. Reconstrucción antes del agente, misma instancia y contexto acotado ------


@pytest.mark.asyncio
async def test_01_rebuild_before_agent_and_bounded_context(monkeypatch):
    events: list[str] = []
    calls: dict[str, object] = {}

    history = [
        {
            "role": "assistant",
            "content": "Feria A (ID: f-1)",
            "turn_index": 0,
            "tool_results": [{
                "tool": "getSiteFairs", "success": True,
                "data": [{"id": "f-1", "name": "Feria A", "status": "OPEN"}],
            }],
        },
    ]

    class FakeStore:
        async def get_history(self, key):
            events.append("read_history")
            return [{"role": "user", "content": "previa"}]

        async def get_full_history(self, key):
            events.append("read_memory")
            calls["rebuild_reads"] = calls.get("rebuild_reads", 0) + 1
            return [dict(message) for message in history]

        async def append_turn(self, key, user_message, assistant_message, *args, **kwargs):
            events.append("write")

    async def fake_agent(provider, context, message, llm_history, execute_tool, **kwargs):
        events.append("agent")
        calls["turn_context"] = kwargs.get("turn_context")
        calls["provider_context"] = dict(context)
        calls["messages"] = llm_history
        return SimpleNamespace(content="respuesta", tool_calls=[], truncated=False), []

    monkeypatch.setattr(main, "conversation_store", FakeStore())
    monkeypatch.setattr(main, "conversation_memories", {})
    monkeypatch.setattr(main, "verify_service_token", lambda authorization: None)
    monkeypatch.setattr(main, "verify_context", lambda token: _context())
    monkeypatch.setattr(main, "run_agent_turn", fake_agent)
    monkeypatch.setattr(main, "build_ui", lambda last_action: ({}, None))
    monkeypatch.setattr(main, "sanitize_assistant_content", lambda content: content)

    await main.chat(
        ChatRequest(message="Dame detalles de esa feria", conversation_id="conv-1", correlation_id="corr-ctx"),
        SimpleNamespace(), "Bearer service-token", "context-token", "corr-ctx",
    )

    # Fase 2.7: se agregó una llamada extra a get_history para log_conversation_memory_rebuilt
    assert events == ["read_history", "read_memory", "read_history", "agent", "write"]
    assert calls["rebuild_reads"] == 1
    turn_context = calls["turn_context"]
    assert isinstance(turn_context, TurnContext)
    assert turn_context.resolved[0].id == "f-1"

    bounded = calls["provider_context"]["conversation_context"]
    assert set(bounded) == {
        "resolved", "unresolved", "ambiguous", "last_query", "selected", "confirmation",
    }
    assert "last_query_results" not in bounded
    assert "history" not in bounded
    # Los mensajes enviados al LLM no se modifican (sin prompts nuevos).
    assert calls["messages"] == [{"role": "user", "content": "previa"}]


# 2. Referencias explícitas ---------------------------------------------------


def test_02_explicit_reference_resolves_selected_entity():
    memory = ConversationMemory()
    memory.session.select_entity("fair", {"id": "f-9", "name": "Feria Nueve"})
    result = memory.resolve_turn_context("Dame detalles de esa feria")
    assert [(item.type, item.id, item.source) for item in result.resolved] == [
        ("fair", "f-9", "selected")
    ]
    assert result.unresolved == []
    assert result.ambiguous == []


# 3. Ordinales con lista reciente --------------------------------------------


def test_03_ordinal_reference_requires_fresh_list():
    memory = _memory_with_fairs("f-1", "f-2", "f-3")
    result = memory.resolve_turn_context("¿Y la segunda feria?")
    assert [(item.type, item.id, item.source) for item in result.resolved] == [
        ("fair", "f-2", "ordinal")
    ]
    assert result.last_query["fresh"] is True

    stale = _memory_with_fairs("f-1", "f-2", "f-3", turn=10)
    stale.session.last_query_turn = 2
    stale_result = stale.resolve_turn_context("¿Y la segunda feria?")
    assert stale_result.resolved == []
    assert stale_result.last_query["fresh"] is False
    # Con evidencia obsoleta, dos candidatos se marcan ambiguos: no se adivina.
    assert any(item.type == "fair" and len(item.candidates) >= 2 for item in stale_result.ambiguous)


# 4. Stands y candidatos ------------------------------------------------------


def test_04_stands_and_candidates_resolve_without_type_confusion():
    memory = ConversationMemory()
    memory._current_turn = 1
    memory.session.select_entity("stand", {"id": "s-7", "name": "A-101"})
    memory.session.select_entity("candidate", {"id": "c-3", "name": "Juan"})
    memory.session.select_entity("fair", {"id": "f-1", "name": "Feria Uno"})

    result = memory.resolve_turn_context("Ese stand y esa candidata")
    resolved = {(item.type, item.id) for item in result.resolved}
    assert ("stand", "s-7") in resolved
    assert ("candidate", "c-3") in resolved
    assert all(not item.id.startswith(("f-", "s-", "c-")) or item.type in {"fair", "stand", "candidate"} for item in result.resolved)

    stands = memory.resolve_turn_context("Muéstrame los stands de esa feria")
    # Sin consulta reciente de listFairStands no se inventa una lista.
    assert stands.resolved == [] or all(item.type != "stands_list" for item in stands.resolved)

    memory.session.last_query_tool = "listFairStands"
    memory.session.last_query_results = [{"id": "s-1"}, {"id": "s-2"}]
    memory.session.last_query_turn = 1
    listed = memory.resolve_turn_context("Muéstrame los stands de esa feria")
    assert any(item.type == "stands_list" and item.id == "f-1" for item in listed.resolved)


# 5. Ambigüedad ---------------------------------------------------------------


def test_05_ambiguous_reference_is_marked_explicitly():
    memory = _memory_with_fairs("f-1", "f-2")
    result = memory.resolve_turn_context("Dame detalles de esa feria")
    assert result.resolved == []
    assert len(result.ambiguous) == 1
    assert result.ambiguous[0].type == "fair"
    assert set(result.ambiguous[0].candidates) == {"f-1", "f-2"}
    assert result.ambiguous[0].reason == "multiple_candidates"


# 6. Referencia inexistente ---------------------------------------------------


def test_06_unknown_reference_is_left_unresolved():
    memory = ConversationMemory()
    result = memory.resolve_turn_context("Háblame de esa feria")
    assert result.resolved == []
    assert result.ambiguous == []
    assert "fair" in result.unresolved


# 7. No contaminación ---------------------------------------------------------


def test_07_no_contamination_between_memories():
    first = _memory_with_fairs("f-1")
    second = _memory_with_fairs("f-2")
    assert first.resolve_turn_context("esa feria").resolved[0].id == "f-1"
    assert second.resolve_turn_context("esa feria").resolved[0].id == "f-2"
    assert first.session.pending_operation is None
    assert second.session.pending_operation is None


# 8/9. Aislamiento org/sede y recuperación tras reinicio con Redis real -------


@pytest.mark.asyncio
async def test_08_09_isolation_and_rebuild_after_restart(redis_store):
    def context_for(sub, org, site):
        return _context(sub, org, site)

    k_org1_site1 = main.conversation_key("conv", context_for("u1", "org1", "site1"))
    k_org1_site2 = main.conversation_key("conv", context_for("u1", "org1", "site2"))
    k_org2_site1 = main.conversation_key("conv", context_for("u1", "org2", "site1"))
    assert len({k_org1_site1, k_org1_site2, k_org2_site1}) == 3

    payload = {
        "content": "Feria Uno (ID: f-1)",
        "turn_index": 0,
        "tool_results": [{
            "tool": "getSiteFairs", "success": True,
            "data": [{"id": "f-1", "name": "Feria Uno", "status": "OPEN"}],
        }],
        "pending_operation": {"tool": "createFair", "operation_id": "op-1", "version": 1},
        "confirmation_state": "awaiting_confirmation",
    }
    for key, fair_id in ((k_org1_site1, "f-1"), (k_org1_site2, "f-2"), (k_org2_site1, "f-3")):
        message = dict(payload)
        message["content"] = f"Feria (ID: {fair_id})"
        message["tool_results"] = [{
            "tool": "getSiteFairs", "success": True,
            "data": [{"id": fair_id, "name": fair_id, "status": "OPEN"}],
        }]
        await redis_store.append_turn(key, "muéstrame ferias", message["content"],
                                      tool_results=message["tool_results"])

    restarted = await ConversationStore.create(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        max_messages=20, ttl_seconds=1800,
    )
    memories = {}
    for key in (k_org1_site1, k_org1_site2, k_org2_site1):
        memories[key] = ConversationMemory.rebuild_from_history(await restarted.get_full_history(key))

    ids = {
        key: memory.resolve_turn_context("Dame detalles de esa feria").resolved[0].id
        for key, memory in memories.items()
    }
    assert ids[k_org1_site1] == "f-1"
    assert ids[k_org1_site2] == "f-2"
    assert ids[k_org2_site1] == "f-3"

    # El estado de confirmación proviene del historial de cada clave, sin cruce.
    confirmation_history = await restarted.get_full_history(k_org1_site1)
    confirmation_history[-1]["pending_operation"] = payload["pending_operation"]
    confirmation_history[-1]["confirmation_state"] = "awaiting_confirmation"
    confirmed = ConversationMemory.rebuild_from_history(confirmation_history)
    assert confirmed.resolve_turn_context("esa feria").confirmation == "awaiting_confirmation"
    assert memories[k_org1_site2].resolve_turn_context("esa feria").confirmation == "none"

    await restarted.redis.close()


# 10. Historial antiguo -------------------------------------------------------


def test_10_legacy_history_without_new_fields():
    history = [
        {"role": "assistant", "content": "Feria Antigua (ID: f-legacy)", "turn_index": 0},
    ]
    memory = ConversationMemory.rebuild_from_history(history)
    result = memory.resolve_turn_context("Dame detalles de esa feria")
    assert result.resolved[0].id == "f-legacy"
    assert result.last_query is None
    assert result.confirmation == "none"
    assert ConversationMemory.from_dict(memory.to_dict()).session.last_query_turn is None


# 11. Estado de confirmación informativo --------------------------------------


def test_11_confirmation_state_is_informational_and_not_duplicated():
    memory = ConversationMemory()
    pending = {"tool": "createFair", "operation_id": "op-2", "version": 2}
    memory.session.pending_operation = pending
    memory.session.confirmation_state = "awaiting_confirmation"

    result = memory.resolve_turn_context("Continúa con el anterior")
    assert result.confirmation == "awaiting_confirmation"
    assert memory.session.pending_operation is pending
    assert "pending_operation" not in result.to_dict()
    assert result.to_dict()["confirmation"] == "awaiting_confirmation"


# 12. Sin herramientas durante la resolución ---------------------------------


def test_12_resolution_never_executes_tools():
    memory = _memory_with_fairs("f-1", "f-2")
    memory.session.select_entity("fair", {"id": "f-1", "name": "Feria Uno"})

    def forbidden(*args, **kwargs):
        raise AssertionError("la resolución no debe ejecutar herramientas")

    original = main.execute_tool
    main.execute_tool = forbidden  # type: ignore[assignment]
    try:
        result = memory.resolve_turn_context("Dame detalles de esa feria")
        assert result.resolved[0].id == "f-1"
    finally:
        main.execute_tool = original


# 13. Sin identificadores inventados ------------------------------------------


def test_13_never_invents_identifiers():
    memory = _memory_with_fairs("f-1")
    result = memory.resolve_turn_context("Muéstrame sus resultados")
    assert result.resolved == []
    assert result.unresolved

    no_candidates = ConversationMemory()
    result2 = no_candidates.resolve_turn_context("El segundo candidato")
    assert result2.resolved == []
    assert result2.unresolved == ["candidate"]

    anaphoric = memory.resolve_turn_context("Continúa con el anterior")
    assert anaphoric.resolved[0].id == "f-1"


# 14. Compatibilidad con el flujo de herramientas y pendientes ---------------


@pytest.mark.asyncio
async def test_14_compatible_with_tool_flow_and_pending_operations(monkeypatch):
    executed: list[str] = []
    saved: dict[str, object] = {}

    async def fake_execute_tool(call, context, message, **kwargs):
        executed.append(call.name)
        if call.name == "createFair":
            return {
                "success": True,
                "data": {"status": "confirmation_required", "tool": "createFair",
                         "operation_id": "op-3", "version": 1},
            }
        return {"success": True, "data": []}

    class FakeLLMResult:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []
            self.usage = {}
            self.latency_ms = 0.0
            self.truncated = False

    class FakeCall:
        def __init__(self, name):
            self.id = "call-1"
            self.name = name
            self.arguments = {}

    class FakeStore:
        async def get_history(self, key):
            return []

        async def get_full_history(self, key):
            return []

        async def append_turn(self, key, user, assistant, *args, **kwargs):
            saved.update(kwargs)

    async def fake_agent(provider, context, message, llm_history, execute_tool, **kwargs):
        first = await execute_tool(FakeCall("getSiteFairs"), context, message)
        second = await execute_tool(FakeCall("createFair"), context, message)
        return FakeLLMResult("Listo"), [first, second]

    monkeypatch.setattr(main, "conversation_store", FakeStore())
    monkeypatch.setattr(main, "conversation_memories", {})
    monkeypatch.setattr(main, "verify_service_token", lambda authorization: None)
    monkeypatch.setattr(main, "verify_context", lambda token: _context())
    monkeypatch.setattr(main, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(main, "run_agent_turn", fake_agent)
    monkeypatch.setattr(main, "build_ui", lambda last_action: ({}, None))
    monkeypatch.setattr(main, "sanitize_assistant_content", lambda content: content)

    response = await main.chat(
        ChatRequest(message="crea una feria", conversation_id="conv-2", correlation_id="corr-ctx"),
        SimpleNamespace(), "Bearer service-token", "context-token", "corr-ctx",
    )

    assert executed == ["getSiteFairs", "createFair"]
    assert saved["pending_operation"]["operation_id"] == "op-3"
    assert saved["confirmation_state"] == "awaiting_confirmation"
    assert response["meta"]["tool_calls"] == 2
