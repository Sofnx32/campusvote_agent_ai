"""Almacén persistente de historial conversacional (patrón dual Redis/Map).

Sigue el mismo patrón que ReplayStore y AdminSessionStore:
- Producción: Redis obligatorio (fail-closed si no configurado)
- Desarrollo: Map en memoria cuando ALLOW_IN_MEMORY_REPLAY=true
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import MutableMapping
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .errors import AgentError


# Script Lua atómico para append_turn en Redis
# Evita lost updates en escrituras concurrentes
APPEND_TURN_SCRIPT = """
local key = KEYS[1]
local max_total = tonumber(ARGV[1])
local ttl_seconds = tonumber(ARGV[2])
local user_message = ARGV[3]
local assistant_message = ARGV[4]
local tool_calls_json = ARGV[5]
local tool_results_json = ARGV[6]
local entities_json = ARGV[7]
local timestamp = ARGV[8]
local now = tonumber(ARGV[9])
local pending_operation_json = ARGV[10]
local confirmation_state = ARGV[11]

-- Obtener historial actual
local raw = redis.call('GET', key)
local messages = {}
if raw then
    local data = cjson.decode(raw)
    messages = data.messages or {}
end

-- Calcular turn_index
local turn_index = 0
for i, msg in ipairs(messages) do
    if msg.role == 'user' then
        turn_index = turn_index + 1
    end
end

-- Añadir mensaje de usuario
table.insert(messages, {
    role = 'user',
    content = user_message,
    tool_calls = cjson.decode(tool_calls_json),
    tool_results = cjson.decode(tool_results_json),
    entities = cjson.decode(entities_json),
    pending_operation = cjson.decode(pending_operation_json),
    confirmation_state = confirmation_state,
    timestamp = timestamp,
    turn_index = turn_index
})

-- Añadir mensaje de assistant
table.insert(messages, {
    role = 'assistant',
    content = assistant_message,
    tool_calls = cjson.decode(tool_calls_json),
    tool_results = cjson.decode(tool_results_json),
    entities = cjson.decode(entities_json),
    pending_operation = cjson.decode(pending_operation_json),
    confirmation_state = confirmation_state,
    timestamp = timestamp,
    turn_index = turn_index
})

-- Recortar a max_total
while #messages > max_total do
    table.remove(messages, 1)
end

-- Guardar con TTL
local expires_at = now + ttl_seconds
local payload = {
    messages = messages,
    updated_at = timestamp,
    expires_at = expires_at
}
redis.call('SET', key, cjson.encode(payload), 'EX', ttl_seconds)

return #messages
"""


class ConversationStore:
    """Almacena y recupera historial conversacional enriquecido.

    Esquema de cada entrada:
    {
        "role": "user|assistant|tool",
        "content": str,
        "tool_calls": list[dict] | None,      # solo role=assistant
        "tool_results": list[dict] | None,    # solo role=tool
        "entities": dict[str, str] | None,    # {"fair_id": "f-123", ...}
        "pending_operation": dict | None,     # operación propuesta por el LLM
        "confirmation_state": str,            # "awaiting_confirmation" | "none"
        "timestamp": "2026-11-17T...",
        "turn_index": int
    }
    """

    def __init__(
        self,
        redis=None,
        clock=None,
        *,
        max_messages: int = 20,
        ttl_seconds: int = 1800,
        node_env: str = "development",
        allow_in_memory: bool = True,
    ):
        self.redis = redis
        self._clock = clock or time.time
        self._max_messages = max_messages
        self._ttl_seconds = ttl_seconds
        self._node_env = node_env
        self._allow_in_memory = allow_in_memory
        self._memory: MutableMapping[str, dict] = {}
        self._prefix = "ai:conversation:"
        self._append_turn_sha: str | None = None

    @staticmethod
    async def create(
        redis_url: str | None = None,
        *,
        max_messages: int | None = None,
        ttl_seconds: int | None = None,
        node_env: str | None = None,
        allow_in_memory: bool | None = None,
        clock=None,
    ) -> "ConversationStore":
        """Factory que respeta la configuración de entorno.

        Si `redis_url` es `None` (no se pasó), se lee de variable de entorno.
        Si `redis_url` es `""` (cadena vacía explícita), se fuerza modo memoria.
        """
        redis_url = redis_url if redis_url is not None else os.getenv("REDIS_URL", "")
        node_env = node_env or os.getenv("NODE_ENV", "development")
        allow_in_memory = (
            allow_in_memory
            if allow_in_memory is not None
            else os.getenv("ALLOW_IN_MEMORY_REPLAY", "true").lower() == "true"
        )
        max_messages = max_messages or int(os.getenv("AI_CONVERSATION_MAX_MESSAGES", "20"))
        ttl_seconds = ttl_seconds or int(os.getenv("AI_CONVERSATION_TTL_SECONDS", "1800"))

        if redis_url:
            # Importación perezosa para no requerir redis en entornos sin él
            import importlib
            redis_asyncio = importlib.import_module("redis.asyncio")
            redis_client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
            # Verificar conexión
            await redis_client.ping()
            store = ConversationStore(
                redis=redis_client,
                clock=clock,
                max_messages=max_messages,
                ttl_seconds=ttl_seconds,
                node_env=node_env,
                allow_in_memory=allow_in_memory,
            )
            # Registrar script Lua para atomicidad
            store._append_turn_sha = await redis_client.script_load(APPEND_TURN_SCRIPT)
            return store

        if node_env == "production" or not allow_in_memory:
            raise AgentError(
                "AI_CONVERSATION_STORE_UNAVAILABLE",
                "Conversation store is not configured",
                503,
                True,
            )

        return ConversationStore(
            redis=None,
            clock=clock,
            max_messages=max_messages,
            ttl_seconds=ttl_seconds,
            node_env=node_env,
            allow_in_memory=allow_in_memory,
        )

    def _key(self, conversation_key: str) -> str:
        return f"{self._prefix}{conversation_key}"

    def _purge_expired(self, conversation_key: str) -> None:
        """Elimina entradas expiradas del Map en memoria."""
        if self.redis:
            return  # Redis maneja TTL nativamente
        entry = self._memory.get(conversation_key)
        if entry and entry.get("expires_at", 0) <= self._clock():
            self._memory.pop(conversation_key, None)

    async def get_history(self, conversation_key: str) -> list[dict]:
        """Recupera el historial de una conversación (sin tool_calls/tool_results para el LLM)."""
        self._purge_expired(conversation_key)

        if self.redis:
            raw = await self.redis.get(self._key(conversation_key))
            if not raw:
                return []
            data = json.loads(raw)
            # Devolver solo los campos que el LLM necesita (role, content)
            return [
                {"role": msg["role"], "content": msg["content"]}
                for msg in data.get("messages", [])
            ]

        entry = self._memory.get(conversation_key)
        if not entry:
            return []
        return [
            {"role": msg["role"], "content": msg["content"]}
            for msg in entry.get("messages", [])
        ]

    async def get_full_history(self, conversation_key: str) -> list[dict]:
        """Recupera el historial COMPLETO (incluye tool_calls, tool_results, entities)."""
        self._purge_expired(conversation_key)

        if self.redis:
            raw = await self.redis.get(self._key(conversation_key))
            if not raw:
                return []
            return json.loads(raw).get("messages", [])

        entry = self._memory.get(conversation_key)
        if not entry:
            return []
        return entry.get("messages", [])

    async def append_turn(
        self,
        conversation_key: str,
        user_message: str,
        assistant_message: str,
        tool_calls: list[dict] | None = None,
        tool_results: list[dict] | None = None,
        entities: dict[str, str] | None = None,
        pending_operation: dict[str, Any] | None = None,
        confirmation_state: str = "none",
    ) -> None:
        """Añade un turno completo (user + assistant) con metadatos enriquecidos.

        En Redis usa script Lua atómico para evitar lost updates en concurrencia.
        """
        if not conversation_key:
            return

        now = self._clock()
        timestamp = datetime.now(timezone.utc).isoformat()
        max_total = self._max_messages * 2

        # Serializar argumentos para Lua
        tool_calls_json = json.dumps(tool_calls) if tool_calls else "[]"
        tool_results_json = json.dumps(tool_results) if tool_results else "[]"
        entities_json = json.dumps(entities) if entities else "{}"
        pending_operation_json = json.dumps(pending_operation) if pending_operation else "{}"
        if confirmation_state not in ("none", "awaiting_confirmation"):
            confirmation_state = "none"

        if self.redis and self._append_turn_sha:
            # Ejecución atómica vía script Lua
            try:
                await self.redis.evalsha(
                    self._append_turn_sha,
                    1,  # número de keys
                    self._key(conversation_key),  # KEYS[1]
                    str(max_total),               # ARGV[1]
                    str(self._ttl_seconds),       # ARGV[2]
                    user_message,                 # ARGV[3]
                    assistant_message,            # ARGV[4]
                    tool_calls_json,              # ARGV[5]
                    tool_results_json,            # ARGV[6]
                    entities_json,                # ARGV[7]
                    timestamp,                    # ARGV[8]
                    str(int(now)),                # ARGV[9]
                    pending_operation_json,       # ARGV[10]
                    confirmation_state,           # ARGV[11]
                )
                return
            except Exception:
                # Si el script falla (ej. script flush), recargar y reintentar una vez
                self._append_turn_sha = await self.redis.script_load(APPEND_TURN_SCRIPT)
                await self.redis.evalsha(
                    self._append_turn_sha,
                    1,
                    self._key(conversation_key),
                    str(max_total),
                    str(self._ttl_seconds),
                    user_message,
                    assistant_message,
                    tool_calls_json,
                    tool_results_json,
                    entities_json,
                    timestamp,
                    str(int(now)),
                    pending_operation_json,
                    confirmation_state,
                )
                return

        # Fallback a memoria (desarrollo sin Redis)
        self._purge_expired(conversation_key)
        entry = self._memory.get(conversation_key)
        messages = entry.get("messages", []) if entry else []

        turn_index = len([m for m in messages if m.get("role") == "user"])

        messages.append({
            "role": "user",
            "content": user_message,
            "tool_calls": None,
            "tool_results": None,
            "entities": None,
            "pending_operation": pending_operation,
            "confirmation_state": confirmation_state,
            "timestamp": timestamp,
            "turn_index": turn_index,
        })

        messages.append({
            "role": "assistant",
            "content": assistant_message,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "entities": entities,
            "pending_operation": pending_operation,
            "confirmation_state": confirmation_state,
            "timestamp": timestamp,
            "turn_index": turn_index,
        })

        if len(messages) > max_total:
            messages = messages[-max_total:]

        expires_at = now + self._ttl_seconds
        payload = {
            "messages": messages,
            "updated_at": timestamp,
            "expires_at": expires_at,
        }
        self._memory[conversation_key] = {**payload, "expires_at": expires_at}

    async def delete_conversation(self, conversation_key: str) -> bool:
        """Elimina una conversación completamente."""
        if self.redis:
            result = await self.redis.delete(self._key(conversation_key))
            return result > 0
        self._purge_expired(conversation_key)
        if conversation_key in self._memory:
            del self._memory[conversation_key]
            return True
        return False

    async def cleanup_expired(self) -> int:
        """Limpia conversaciones expiradas (solo modo memoria; Redis usa TTL)."""
        if self.redis:
            return 0
        now = self._clock()
        expired = [
            key for key, entry in self._memory.items()
            if entry.get("expires_at", 0) <= now
        ]
        for key in expired:
            del self._memory[key]
        return len(expired)