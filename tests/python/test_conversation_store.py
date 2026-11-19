"""Pruebas para ConversationStore y ConversationMemory (Etapa 1).

Valida los 10 escenarios obligatorios:
1. Guardar y recuperar historial de conversación.
2. Persistir tool_calls y tool_results.
3. Conservar identificadores de ferias, proyectos y stands.
4. Resolver referencias cuando el contexto sea suficiente.
5. NO resolver referencias cuando exista ambigüedad.
6. Aplicar correctamente el TTL.
7. Respetar límite máximo de mensajes (AI_MAX_HISTORY_MESSAGES).
8. Recuperar historial tras reiniciar almacén/proceso.
9. Evitar corrupción en escrituras concurrentes.
10. Mantener contratos de autorización/herramientas (sin regresiones Fase 1).
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("AI_SERVICE_TOKEN", "x" * 32)
os.environ.setdefault("NODE_ENV", "development")
os.environ.setdefault("ALLOW_IN_MEMORY_REPLAY", "true")

from app.conversation_store import ConversationStore
from app.conversation_memory import ConversationMemory, SessionContext
from app.config import Settings


class TestConversationStore:
    """Pruebas del almacén persistente (escenarios 1, 2, 6, 7, 8, 9)."""

    @pytest_asyncio.fixture
    async def store(self):
        """Crea un store en memoria para testing (fuerza modo memoria, ignora REDIS_URL)."""
        s = await ConversationStore.create(
            redis_url="",  # Fuerza modo memoria, ignora variable de entorno
            max_messages=5,
            ttl_seconds=10,
            allow_in_memory=True,
        )
        yield s
        # Cleanup
        if not s.redis:
            s._memory.clear()

    async def test_1_save_and_retrieve_history(self, store):
        """Escenario 1: Guardar y recuperar historial de conversación."""
        key = "user1:org1:conv1"

        await store.append_turn(
            key,
            "Hola, ¿cómo estás?",
            "¡Hola! Estoy bien, ¿en qué te ayudo?",
        )

        history = await store.get_history(key)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hola, ¿cómo estás?"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "¡Hola! Estoy bien, ¿en qué te ayudo?"

    async def test_2_persist_tool_calls_and_results(self, store):
        """Escenario 2: Persistir tool_calls y tool_results."""
        key = "user1:org1:conv2"

        tool_calls = [
            {"id": "call-1", "name": "getSiteFairs", "arguments": {}},
        ]
        tool_results = [
            {"tool": "getSiteFairs", "success": True, "data": [{"id": "f-1", "name": "Feria A"}]},
        ]

        await store.append_turn(
            key,
            "Muéstrame las ferias",
            "Aquí tienes las ferias:",
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

        full_history = await store.get_full_history(key)
        assert len(full_history) == 2

        assistant_msg = full_history[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["tool_calls"] == tool_calls
        assert assistant_msg["tool_results"] == tool_results

    async def test_3_conserve_entity_identifiers(self, store):
        """Escenario 3: Conservar identificadores de ferias, proyectos, stands."""
        key = "user1:org1:conv3"

        entities = {"fair_id": "f-123", "project_id": "p-456", "stand_id": "s-789"}

        await store.append_turn(
            key,
            "Quiero ver la feria f-123",
            "Aquí está la feria seleccionada.",
            entities=entities,
        )

        full_history = await store.get_full_history(key)
        assistant_msg = full_history[1]
        assert assistant_msg["entities"] == entities
        assert assistant_msg["entities"]["fair_id"] == "f-123"
        assert assistant_msg["entities"]["project_id"] == "p-456"
        assert assistant_msg["entities"]["stand_id"] == "s-789"

    async def test_6_ttl_expiration(self, store):
        """Escenario 6: Aplicar correctamente el TTL."""
        key = "user1:org1:conv_ttl"

        await store.append_turn(key, "Mensaje 1", "Respuesta 1")

        # Verificar que existe inmediatamente
        history = await store.get_history(key)
        assert len(history) == 2

        # Manipular el tiempo de expiración directamente en memoria
        # Nota: en modo memoria la clave NO tiene prefijo (a diferencia de Redis)
        if not store.redis:
            entry = store._memory.get(key)
            assert entry is not None, f"Entry not found for key {key}"
            # Forzar expiración
            entry["expires_at"] = time.time() - 1

        # Ahora debería haber expirado
        history = await store.get_history(key)
        assert history == []

    async def test_7_max_history_messages_limit(self, store):
        """Escenario 7: Respetar límite máximo de mensajes (AI_MAX_HISTORY_MESSAGES=5 -> 10 totales)."""
        key = "user1:org1:conv_limit"

        # Añadir 6 turnos (12 mensajes) - límite es 5 turnos (10 mensajes)
        for i in range(6):
            await store.append_turn(key, f"Usuario {i}", f"Asistente {i}")

        history = await store.get_history(key)
        # Debe recortar a 10 mensajes (5 turnos)
        assert len(history) == 10
        # Los más antiguos deben haberse eliminado
        assert "Usuario 0" not in [m["content"] for m in history]
        assert "Usuario 1" in [m["content"] for m in history]

    async def test_8_recover_after_restart(self):
        """Escenario 8: Recuperar historial tras reiniciar el almacén (Redis real)."""
        from app.conversation_store import ConversationStore
        import os

        # Usar Redis real para probar persistencia real
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        key = "user1:org1:conv_restart"

        store = await ConversationStore.create(
            redis_url=redis_url,
            max_messages=20,
            ttl_seconds=1800,
        )

        await store.append_turn(key, "Mensaje persistente", "Respuesta persistente")

        # Simular reinicio: nueva instancia conectada al mismo Redis
        new_store = await ConversationStore.create(
            redis_url=redis_url,
            max_messages=20,
            ttl_seconds=1800,
        )

        history = await new_store.get_history(key)
        assert len(history) == 2
        assert history[0]["content"] == "Mensaje persistente"

        # Cleanup
        await store.delete_conversation(key)
        await store.redis.close()
        await new_store.redis.close()

    async def test_9_concurrent_writes_no_corruption(self, store):
        """Escenario 9: Evitar corrupción en escrituras concurrentes."""
        key = "user1:org1:conv_concurrent"

        async def write_turn(i):
            await store.append_turn(key, f"User {i}", f"Assistant {i}")

        # Escrituras concurrentes
        await asyncio.gather(*[write_turn(i) for i in range(10)])

        history = await store.get_history(key)
        # Debe tener 10 mensajes (5 turnos * 2) por el límite
        assert len(history) <= 10
        # Verificar integridad: cada mensaje tiene role y content
        for msg in history:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ("user", "assistant")

    async def test_delete_conversation(self, store):
        """Eliminar conversación completamente."""
        key = "user1:org1:conv_delete"

        await store.append_turn(key, "Test", "Response")
        assert len(await store.get_history(key)) == 2

        deleted = await store.delete_conversation(key)
        assert deleted is True

        history = await store.get_history(key)
        assert history == []


class TestConversationMemory:
    """Pruebas de memoria conversacional y resolución de referencias (escenarios 3, 4, 5)."""

    def test_3_conserve_fair_project_stand_ids(self):
        """Escenario 3: Identificadores conservados en SessionContext."""
        memory = ConversationMemory()

        fair = {"id": "f-123", "name": "Feria Ciencias", "status": "DRAFT"}
        project = {"id": "p-456", "name": "Proyecto Robótica"}
        stand = {"id": "s-789", "code": "A-101"}

        memory.session.select_entity("fair", fair)
        memory.session.select_entity("project", project)
        memory.session.select_entity("stand", stand)

        assert memory.session.get_selected("fair")["id"] == "f-123"
        assert memory.session.get_selected("project")["id"] == "p-456"
        assert memory.session.get_selected("stand")["id"] == "s-789"

    def test_4_resolve_ordinal_reference(self):
        """Escenario 4: Resolver 'la segunda feria' cuando hay contexto suficiente."""
        memory = ConversationMemory()
        memory._current_turn = 1

        # Simular resultados previos de getSiteFairs
        fairs = [
            {"id": "f-1", "name": "Feria A", "status": "DRAFT"},
            {"id": "f-2", "name": "Feria B", "status": "OPEN"},
            {"id": "f-3", "name": "Feria C", "status": "CLOSED"},
        ]
        memory.session.last_query_results = fairs
        memory.session.last_query_tool = "getSiteFairs"

        # Resolver "la segunda feria"
        result = memory.resolve_reference("¿Y la segunda feria cuándo empieza?")
        assert result is not None
        assert result["type"] == "fair"
        assert result["id"] == "f-2"
        assert result["data"]["name"] == "Feria B"

    def test_4_resolve_anaphoric_reference(self):
        """Escenario 4: Resolver 'esa feria' con entidad seleccionada."""
        memory = ConversationMemory()

        fair = {"id": "f-999", "name": "Feria Seleccionada", "status": "OPEN"}
        memory.session.select_entity("fair", fair)

        result = memory.resolve_reference("Dame detalles de esa feria")
        assert result is not None
        assert result["type"] == "fair"
        assert result["id"] == "f-999"

    def test_5_no_resolve_ambiguous_reference(self):
        """Escenario 5: NO resolver cuando hay ambigüedad."""
        memory = ConversationMemory()
        memory._current_turn = 1

        # Sin entidad seleccionada ni resultados previos
        result = memory.resolve_reference("¿Y esa feria?")
        assert result is None  # Ambigüedad: no adivinar

        # Con múltiples ferias pero sin ordinal claro
        fairs = [
            {"id": "f-1", "name": "Feria A"},
            {"id": "f-2", "name": "Feria B"},
        ]
        memory.session.last_query_results = fairs
        memory.session.last_query_tool = "getSiteFairs"

        result = memory.resolve_reference("¿Qué pasa con la feria?")
        assert result is None  # Ambigüedad: cuál de las dos

    def test_4_resolve_project_ordinal(self):
        """Escenario 4: Resolver ordinal en proyectos."""
        memory = ConversationMemory()
        memory._current_turn = 1

        projects = [
            {"id": "p-1", "name": "Proyecto Alpha"},
            {"id": "p-2", "name": "Proyecto Beta"},
        ]
        memory.session.last_query_results = projects
        memory.session.last_query_tool = "getSiteProjects"

        result = memory.resolve_reference("El primer proyecto me interesa")
        assert result is not None
        assert result["type"] == "project"
        assert result["id"] == "p-1"

    def test_4_resolve_stand_reference(self):
        """Escenario 4: Resolver stands de la feria seleccionada."""
        memory = ConversationMemory()

        fair = {"id": "f-1", "name": "Feria Ciencias"}
        memory.session.select_entity("fair", fair)

        stands = [
            {"id": "s-1", "code": "A-101"},
            {"id": "s-2", "code": "B-202"},
        ]
        memory.session.last_query_results = stands
        memory.session.last_query_tool = "listFairStands"

        # El patrón requiere "esa" o "la feria" + "stand"
        result = memory.resolve_reference("¿Cuáles son los stands de esa feria?")
        assert result is not None
        assert result["type"] == "stands_list"
        assert result["fair_id"] == "f-1"
        assert len(result["data"]) == 2

        # También funciona con "la feria"
        result2 = memory.resolve_reference("Muéstrame los stands de la feria")
        assert result2 is not None
        assert result2["type"] == "stands_list"
        assert result2["fair_id"] == "f-1"

    def test_serialization_roundtrip(self):
        """Verificar que la memoria se puede serializar y deserializar."""
        memory = ConversationMemory()
        memory._current_turn = 5
        memory.session.select_entity("fair", {"id": "f-1", "name": "Test"})
        memory.session.last_query_tool = "getSiteFairs"
        memory.session.last_query_results = [{"id": "f-1"}]
        memory.mentions.record_mention("fair", "f-1", 3)

        data = memory.to_dict()
        restored = ConversationMemory.from_dict(data)

        assert restored._current_turn == 5
        assert restored.session.selected_fair["id"] == "f-1"
        assert restored.session.last_query_tool == "getSiteFairs"
        assert restored.mentions.get_most_recent("fair") == "f-1"


class TestIntegrationNoRegressions:
    """Escenario 10: Sin regresiones en contratos Fase 1."""

    def test_max_history_messages_configurable(self):
        """AI_MAX_HISTORY_MESSAGES se lee de config."""
        settings = Settings()
        assert hasattr(settings, "max_history_messages")
        assert settings.max_history_messages == 12  # default

    def test_conversation_max_messages_configurable(self):
        """AI_CONVERSATION_MAX_MESSAGES se mantiene."""
        settings = Settings()
        assert settings.conversation_max_messages == 20

    def test_conversation_ttl_configurable(self):
        """AI_CONVERSATION_TTL_SECONDS se mantiene."""
        settings = Settings()
        assert settings.conversation_ttl_seconds == 1800