"""Etapa 2.2: integridad asíncrona, aislamiento por sede y reconstrucción.

Las pruebas A–C usan el endpoint real de Python con una store simulada o Redis
real local. La suite completa y las pruebas Node se ejecutan por separado.
"""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

os.environ.setdefault("AI_SERVICE_TOKEN", "x" * 32)
os.environ.setdefault("NODE_ENV", "development")
os.environ.setdefault("ALLOW_IN_MEMORY_REPLAY", "true")

from app import main  # noqa: E402
from app.conversation_memory import ConversationMemory  # noqa: E402
from app.conversation_store import ConversationStore  # noqa: E402
from app.models import ChatRequest  # noqa: E402


def _context(sub: str = "u1", organization_id: str = "org1", site_id: str = "site1") -> dict:
    return {
        "sub": sub,
        "organization_id": organization_id,
        "site_id": site_id,
        "correlation_id": "corr-1",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=25)).isoformat(),
    }


@pytest_asyncio.fixture
async def redis_store():
    """Store conectada a Redis real local; limpia solo claves de esta etapa."""
    store = await ConversationStore.create(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        max_messages=20,
        ttl_seconds=1800,
    )
    yield store
    if store.redis:
        keys = await store.redis.keys("ai:conversation:test:integrity:*")
        if keys:
            await store.redis.delete(*keys)
        await store.redis.close()


# A. Historial ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_history_tool_metadata_and_restart(redis_store):
    """Guarda un turno, lo recupera tras reinicio y conserva tool metadata."""
    key = "test:integrity:history"
    tool_calls = [{"id": "call-1", "name": "getSiteFairs", "arguments": {}}]
    tool_results = [{
        "tool": "getSiteFairs",
        "success": True,
        "data": [
            {"id": "f-1", "name": "Feria A"},
            {"id": "f-2", "name": "Feria B"},
        ],
    }]

    await redis_store.append_turn(
        key,
        "Muéstrame las ferias",
        "Aquí tienes dos ferias",
        tool_calls=tool_calls,
        tool_results=tool_results,
        entities={"fair_id": "f-2"},
        pending_operation={"tool": "createFair", "operation_id": "op-1", "version": 1},
        confirmation_state="awaiting_confirmation",
    )

    restarted = await ConversationStore.create(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        max_messages=20,
        ttl_seconds=1800,
    )
    full = await restarted.get_full_history(key)
    assert len(full) == 2
    assert full[1]["tool_calls"] == tool_calls
    assert full[1]["tool_results"] == tool_results
    assert full[1]["entities"] == {"fair_id": "f-2"}
    assert full[1]["pending_operation"]["operation_id"] == "op-1"
    assert full[1]["confirmation_state"] == "awaiting_confirmation"

    llm_history = await restarted.get_history(key)
    assert llm_history == [
        {"role": "user", "content": "Muéstrame las ferias"},
        {"role": "assistant", "content": "Aquí tienes dos ferias"},
    ]

    memory = ConversationMemory.rebuild_from_history(full)
    assert memory.session.last_query_tool == "getSiteFairs"
    assert len(memory.session.last_query_results) == 2
    assert memory.session.pending_operation["tool"] == "createFair"
    assert memory.session.confirmation_state == "awaiting_confirmation"

    await restarted.delete_conversation(key)
    await restarted.redis.close()


# B. Asincronía y orden ------------------------------------------------------


@pytest.mark.asyncio
async def test_b_async_wrappers_and_read_agent_write_order(monkeypatch):
    """Los wrappers son async y el endpoint espera lectura → agente → escritura."""
    assert inspect.iscoroutinefunction(main.get_history)
    assert inspect.iscoroutinefunction(main.get_full_history) if hasattr(main, "get_full_history") else True
    assert inspect.iscoroutinefunction(main.save_turn)
    assert inspect.iscoroutinefunction(main.get_or_create_memory)

    events: list[tuple] = []

    class FakeStore:
        async def get_history(self, key):
            events.append(("read_history", key))
            return [{"role": "user", "content": "previa"}]

        async def get_full_history(self, key):
            events.append(("read_memory", key))
            return [{"role": "assistant", "content": "previa", "turn_index": 0}]

        async def append_turn(self, key, user_message, assistant_message, *args, **kwargs):
            events.append(("write", key))

    async def fake_agent(provider, context, message, history, execute_tool, **kwargs):
        events.append(("agent", tuple(history)))
        return SimpleNamespace(content="respuesta", tool_calls=[], truncated=False), []

    monkeypatch.setattr(main, "conversation_store", FakeStore())
    monkeypatch.setattr(main, "conversation_memories", {})
    monkeypatch.setattr(main, "verify_service_token", lambda authorization: None)
    monkeypatch.setattr(main, "verify_context", lambda token: _context())
    monkeypatch.setattr(main, "run_agent_turn", fake_agent)
    monkeypatch.setattr(main, "build_ui", lambda last_action: ({}, None))
    monkeypatch.setattr(main, "sanitize_assistant_content", lambda content: content)

    response = await main.chat(
        ChatRequest(message="hola", conversation_id="conv-1", correlation_id="corr-1"),
        SimpleNamespace(),
        "Bearer service-token",
        "context-token",
        "corr-1",
    )

    assert [event[0] for event in events] == ["read_history", "read_memory", "agent", "write"]
    assert events[0][1] == "u1:org1:site1:conv-1"
    assert response["meta"]["tool_calls"] == 0


# C. Aislamiento -------------------------------------------------------------


def test_c_conversation_key_requires_verified_site():
    assert main.conversation_key("conv-1", _context()) == "u1:org1:site1:conv-1"
    assert main.conversation_key("conv-1", _context(site_id="site2")) == "u1:org1:site2:conv-1"
    assert main.conversation_key("conv-1", _context(organization_id="org2")) == "u1:org2:site1:conv-1"
    # Sin site_id verificado: falla cerrado, nunca accede a Redis.
    assert main.conversation_key("conv-1", {"sub": "u1", "organization_id": "org1"}) is None
    assert main.conversation_key(None, _context()) is None


@pytest.mark.asyncio
async def test_c_isolation_between_orgs_sites_conversations_and_old_keys():
    store = await ConversationStore.create(redis_url="", allow_in_memory=True)

    k_org1_site1 = main.conversation_key("conv", _context())
    k_org2_site1 = main.conversation_key("conv", _context(organization_id="org2"))
    k_org1_site2 = main.conversation_key("conv", _context(site_id="site2"))
    k_org1_site1_other = main.conversation_key("conv-other", _context())

    for key, marker in (
        (k_org1_site1, "org1-site1"),
        (k_org2_site1, "org2-site1"),
        (k_org1_site2, "org1-site2"),
        (k_org1_site1_other, "org1-site1-other"),
    ):
        await store.append_turn(key, marker, "respuesta")

    assert (await store.get_history(k_org1_site1))[0]["content"] == "org1-site1"
    for other in (k_org2_site1, k_org1_site2, k_org1_site1_other):
        assert await store.get_history(other) != await store.get_history(k_org1_site1)

    # Clave antigua sin site_id: se lee, pero NUNCA se usa como fallback de la nueva.
    legacy_key = "u1:org1:conv"
    await store.append_turn(legacy_key, "historial antiguo", "respuesta")
    assert (await store.get_history(legacy_key))[0]["content"] == "historial antiguo"
    assert (await store.get_history(k_org1_site1))[0]["content"] == "org1-site1"


# D. Memoria -----------------------------------------------------------------


def test_d_rebuild_restores_entities_results_and_confirmation_state():
    history = [
        {"role": "user", "content": "Muéstrame ferias", "turn_index": 0},
        {
            "role": "assistant",
            "content": "Aquí tienes dos ferias",
            "turn_index": 0,
            "tool_results": [{
                "tool": "getSiteFairs",
                "success": True,
                "data": [
                    {"id": "f-1", "name": "Feria A"},
                    {"id": "f-2", "name": "Feria B"},
                ],
            }],
        },
        {"role": "user", "content": "Quiero la feria f-2", "turn_index": 1},
        {
            "role": "assistant",
            "content": "Elegiste Feria B (ID: f-2). Stand A-101 (ID: s-1). Candidato Juan (ID: c-1).",
            "turn_index": 1,
            "entities": {"stand_id": "s-1", "candidate_id": "c-1"},
            "pending_operation": {"tool": "createFair", "operation_id": "op-2", "version": 2},
            "confirmation_state": "awaiting_confirmation",
        },
    ]

    memory = ConversationMemory.rebuild_from_history(history)
    assert memory.session.last_query_tool == "getSiteFairs"
    assert len(memory.session.last_query_results) == 2
    assert memory.session.selected_fair["id"] == "f-2"
    assert memory.session.selected_stand["id"] == "s-1"
    assert memory.session.selected_candidate["id"] == "c-1"
    assert memory.mentions.get_most_recent("fair") == "f-2"
    assert memory.mentions.get_most_recent("candidate") == "c-1"
    assert memory.session.pending_operation["operation_id"] == "op-2"
    assert memory.session.confirmation_state == "awaiting_confirmation"
    assert memory.resolve_reference("Dame detalles de esa feria")["id"] == "f-2"
    assert memory.resolve_reference("¿Y la segunda feria?")["id"] == "f-2"


def test_d_rebuilt_memories_do_not_share_state():
    base = [
        {"role": "assistant", "content": "Feria A (ID: f-1)", "turn_index": 0,
         "entities": {"fair_id": "f-1"}},
    ]
    other = [
        {"role": "assistant", "content": "Feria B (ID: f-2)", "turn_index": 0,
         "entities": {"fair_id": "f-2"}},
    ]
    first = ConversationMemory.rebuild_from_history(base)
    second = ConversationMemory.rebuild_from_history(other)

    assert first.mentions.get_most_recent("fair") == "f-1"
    assert second.mentions.get_most_recent("fair") == "f-2"
    assert first.resolve_reference("esa feria")["id"] == "f-1"
    assert second.resolve_reference("esa feria")["id"] == "f-2"
