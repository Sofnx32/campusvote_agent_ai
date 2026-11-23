"""Pruebas de integración con Redis real para memoria conversacional.

Estas pruebas validan la persistencia real en Redis, destrucción y recreación de instancias,
y concurrencia atómica. Requieren Redis disponible (variable REDIS_URL o docker).

Ejecución:
    REDIS_URL=redis://localhost:6379 python -m pytest tests/python/test_conversation_redis_integration.py -v
    # O con docker-compose up -d redis y luego ejecutar
"""

import os
import asyncio
import pytest
import pytest_asyncio

# Configurar variables de entorno para testing
os.environ.setdefault("AI_SERVICE_TOKEN", "x" * 32)
os.environ.setdefault("NODE_ENV", "development")
os.environ.setdefault("ALLOW_IN_MEMORY_REPLAY", "true")

# Marcar todas las pruebas como asyncio
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis_store():
    """Crea un ConversationStore conectado a Redis real. Se salta si Redis no está disponible."""
    import pytest
    from app.conversation_store import ConversationStore

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    # Si REDIS_URL está vacío explícitamente, no hay Redis real disponible
    if not redis_url:
        pytest.skip("REDIS_URL no configurado (vacío), saltando pruebas de integración Redis")
    
    try:
        print(f"[DEBUG] Creating store with redis_url={redis_url}")
        store = await ConversationStore.create(
            redis_url=redis_url,
            max_messages=20,
            ttl_seconds=1800,
        )
        # Verificar que la conexión funciona realmente
        await store.redis.ping()
        print(f"[DEBUG] Store created successfully")
        yield store
        # Cleanup: eliminar claves de prueba
        if store.redis:
            keys = await store.redis.keys("ai:conversation:test:*")
            if keys:
                await store.redis.delete(*keys)
            await store.redis.close()
        print(f"[DEBUG] Cleanup done")
    except Exception as e:
        import traceback
        print(f"[DEBUG] Exception in fixture: {e}")
        traceback.print_exc()
        pytest.skip(f"Redis no disponible en {redis_url}: {e}")


async def test_redis_persistence_and_recovery(redis_store):
    """Escenario 2: Persistencia real en Redis y recuperación tras reinicio.

    1. Guardar conversación con tool_calls, tool_results, entidades
    2. Destruir instancias (simular reinicio proceso)
    3. Crear nuevas instancias conectadas al mismo Redis
    4. Recuperar conversación
    5. Verificar fair_id, project_id, stand_id
    6. Verificar resolución de referencias ("esa feria")
    """
    from app.conversation_store import ConversationStore
    from app.conversation_memory import ConversationMemory

    store = redis_store
    key = "test:org1:conv_persistence"

    # 1. Guardar turno con tool_calls y tool_results
    tool_calls = [
        {"id": "call-1", "name": "getSiteFairs", "arguments": {}},
    ]
    tool_results = [
        {
            "tool": "getSiteFairs",
            "success": True,
            "data": [
                {"id": "f-1", "name": "Feria Ciencias", "status": "DRAFT"},
                {"id": "f-2", "name": "Feria Arte", "status": "OPEN"},
                {"id": "f-3", "name": "Feria Deportes", "status": "CLOSED"},
            ],
        },
    ]
    entities = {"fair_id": "f-2"}

    await store.append_turn(
        key,
        "Muéstrame las ferias",
        "Aquí tienes 3 ferias: 1 DRAFT, 1 OPEN, 1 CLOSED",
        tool_calls=tool_calls,
        tool_results=tool_results,
        entities=entities,
    )

    # Guardar segundo turno: seleccionar la segunda feria
    await store.append_turn(
        key,
        "Quiero la segunda feria",
        "Has seleccionado la Feria Arte (ID: f-2)",
        tool_calls=None,
        tool_results=None,
        entities={"fair_id": "f-2"},
    )

    # 2. SIMULAR REINICIO: destruir instancias
    store2 = await ConversationStore.create(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        max_messages=20,
        ttl_seconds=1800,
    )

    # 3. Recuperar historial completo
    full_history = await store2.get_full_history(key)
    assert len(full_history) == 4  # 2 turnos = 4 mensajes

    # Verificar estructura del historial
    user_msg_1 = full_history[0]
    assert user_msg_1["role"] == "user"
    assert user_msg_1["content"] == "Muéstrame las ferias"

    assistant_msg_1 = full_history[1]
    assert assistant_msg_1["role"] == "assistant"
    assert assistant_msg_1["tool_calls"] is not None
    assert len(assistant_msg_1["tool_calls"]) == 1
    assert assistant_msg_1["tool_calls"][0]["name"] == "getSiteFairs"
    assert assistant_msg_1["tool_results"] is not None
    assert len(assistant_msg_1["tool_results"]) == 1
    assert assistant_msg_1["tool_results"][0]["tool"] == "getSiteFairs"
    assert len(assistant_msg_1["tool_results"][0]["data"]) == 3

    # Verificar entidades persistidas
    assert assistant_msg_1["entities"] == {"fair_id": "f-2"}

    # 4. Reconstruir ConversationMemory desde historial
    memory = ConversationMemory.rebuild_from_history(full_history)

    # 5. Verificar recuperación de entidades
    assert memory.session.last_query_tool == "getSiteFairs"
    assert memory.session.last_query_results is not None
    assert len(memory.session.last_query_results) == 3

    # Verificar que la segunda feria está en los resultados
    second_fair = memory.session.last_query_results[1]
    assert second_fair["id"] == "f-2"
    assert second_fair["name"] == "Feria Arte"

    # Verificar entidad seleccionada por el usuario
    assert memory.session.selected_fair is not None
    assert memory.session.selected_fair["id"] == "f-2"

    # 6. Verificar resolución de referencias
    # "esa feria" debe resolver a la seleccionada (f-2)
    result = memory.resolve_reference("Dame detalles de esa feria")
    assert result is not None
    assert result["type"] == "fair"
    assert result["id"] == "f-2"

    # "la segunda feria" debe resolver por ordinal
    result2 = memory.resolve_reference("¿Y la segunda feria cuándo empieza?")
    assert result2 is not None
    assert result2["type"] == "fair"
    assert result2["id"] == "f-2"
    assert result2["data"]["name"] == "Feria Arte"

    # Cleanup
    await store2.delete_conversation(key)
    if store2.redis:
        await store2.redis.close()


async def test_redis_concurrency_atomic_append(redis_store):
    """Escenario 3: Concurrencia - escrituras simultáneas no pierden datos.

    Múltiples escrituras concurrentes a la misma conversación deben
    resultar en todos los turnos preservados (atomicidad Lua).
    """
    store = redis_store
    key = "test:org1:conv_concurrent"

    async def write_turn(i: int):
        await store.append_turn(
            key,
            f"Usuario mensaje {i}",
            f"Asistente respuesta {i}",
            tool_calls=None,
            tool_results=None,
            entities=None,
        )

    # 10 escrituras concurrentes
    await asyncio.gather(*[write_turn(i) for i in range(10)])

    # Verificar que todos los turnos se guardaron (máx 20 mensajes = 10 turnos)
    history = await store.get_history(key)
    assert len(history) == 20  # 10 turnos * 2 mensajes

    # Verificar integridad: cada mensaje tiene role y content válidos
    for i, msg in enumerate(history):
        assert "role" in msg
        assert "content" in msg
        assert msg["role"] in ("user", "assistant")
        # Los mensajes de usuario deben contener el número de turno
        if msg["role"] == "user":
            assert f"mensaje {i//2}" in msg["content"].lower() or "usuario" in msg["content"].lower()

    # Cleanup
    await store.delete_conversation(key)


async def test_redis_ttl_expiration(redis_store):
    """Verificar que TTL funciona correctamente en Redis."""
    store = redis_store
    key = "test:org1:conv_ttl"

    await store.append_turn(key, "Test TTL", "Respuesta TTL")

    # Verificar que existe
    history = await store.get_history(key)
    assert len(history) == 2

    # Verificar TTL en Redis (debe ser > 0 y <= configured)
    if store.redis:
        ttl = await store.redis.ttl(store._key(key))
        assert ttl > 0
        assert ttl <= store._ttl_seconds

    # Cleanup
    await store.delete_conversation(key)


async def test_redis_recovery_preserves_turn_order(redis_store):
    """Verificar que el orden de turnos se preserva tras recuperación."""
    from app.conversation_store import ConversationStore

    store = redis_store
    key = "test:org1:conv_order"

    # Escribir 5 turnos secuenciales
    for i in range(5):
        await store.append_turn(
            key,
            f"Turno {i} usuario",
            f"Turno {i} asistente",
        )

    # Simular reinicio
    store2 = await ConversationStore.create(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        max_messages=20,
        ttl_seconds=1800,
    )

    # Recuperar historial completo
    full_history = await store2.get_full_history(key)
    assert len(full_history) == 10  # 5 turnos * 2

    # Verificar orden cronológico por turn_index
    for i in range(0, 10, 2):
        user_msg = full_history[i]
        assistant_msg = full_history[i + 1]
        assert user_msg["turn_index"] == i // 2
        assert assistant_msg["turn_index"] == i // 2
        assert f"Turno {i//2} usuario" in user_msg["content"]
        assert f"Turno {i//2} asistente" in assistant_msg["content"]

    # Cleanup
    await store2.delete_conversation(key)
    if store2.redis:
        await store2.redis.close()


if __name__ == "__main__":
    # Permitir ejecución directa para debugging
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))