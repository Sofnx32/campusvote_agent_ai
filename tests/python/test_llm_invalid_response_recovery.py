"""Regresión: respuestas vacías del LLM después de una lectura exitosa.

El error de producción ``LLM_INVALID_RESPONSE`` aparecía cuando el modelo no
redactaba el texto final tras ejecutar ``listFairCategories``. El turno debe
responderse de forma determinista, sin UUIDs y sin romper la conversación.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import main
from app.conversation_memory import TurnContext
from app.models import ChatRequest
from app.providers.tool_selector import select_tool_names


def _context() -> dict:
    return {
        "sub": "u1",
        "organization_id": "org1",
        "site_id": "site1",
        "correlation_id": "corr-recovery",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=25)).isoformat(),
    }


class _FakeStore:
    async def get_history(self, key):
        return []

    async def get_full_history(self, key):
        return []

    async def append_turn(self, key, user, assistant, *args, **kwargs):
        return None


class _FakeMemory:
    def extract_entities_from_message(self, message):
        return {}

    def resolve_turn_context(self, message):
        return TurnContext(selected={"fair": {"id": "f-1", "name": "Feria Ciencias"}})


class _EmptyFinalResult:
    """Simula el ``content=''`` final devuelto por el proveedor."""

    def __init__(self):
        self.content = ""
        self.tool_calls = []
        self.usage = {}
        self.latency_ms = 0.0
        self.truncated = False
        self.allowed_tools = frozenset()


@pytest.mark.asyncio
async def test_list_fair_categories_empty_with_empty_llm_content_is_deterministic(monkeypatch):
    """``listFairCategories -> []`` con LLM sin texto no debe lanzar error."""
    tool_result = {
        "success": True,
        "data": [],
        "meta": {"tool": "listFairCategories"},
    }

    async def fake_agent(provider, context, message, history, execute_tool, **kwargs):
        return _EmptyFinalResult(), [tool_result]

    monkeypatch.setattr(main, "conversation_store", _FakeStore())
    monkeypatch.setattr(main, "conversation_memories", {})
    monkeypatch.setattr(main, "verify_service_token", lambda authorization: None)
    monkeypatch.setattr(main, "verify_context", lambda token: _context())
    async def fake_memory(*args, **kwargs):
        return _FakeMemory()

    monkeypatch.setattr(main, "get_or_create_memory", fake_memory)
    monkeypatch.setattr(main, "direct_response", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "run_agent_turn", fake_agent)
    monkeypatch.setattr(main, "build_ui", lambda last_action: ({}, None))
    monkeypatch.setattr(main, "sanitize_assistant_content", lambda content: content)

    response = await main.chat(
        ChatRequest(
            message="muéstrame las categorías",
            conversation_id="conv-recovery",
            correlation_id="corr-recovery",
        ),
        SimpleNamespace(),
        "Bearer service-token",
        "context-token",
        "corr-recovery",
    )

    content = response["message"]["content"]
    assert "Feria Ciencias" in content
    assert "aún no tiene categorías registradas" in content
    assert "Dime el nombre de la categoría que deseas crear para comenzar." in content
    assert "f-1" not in content
    assert response["meta"]["tool_calls"] == 1


def test_affirmation_with_selected_fair_announces_category_rubric_tools():
    """Una afirmación con feria seleccionada anuncia el conjunto del prompt."""
    context = {
        "conversation_context": {
            "selected": {"fair": {"id": "f-1", "name": "Feria Ciencias"}},
        }
    }
    messages = [{"role": "user", "content": "sí"}]

    selected = set(select_tool_names(context, messages))

    assert {
        "getSiteFairs",
        "listFairCategories",
        "getFairRubric",
        "createFairCategory",
        "setup_category_with_rubric",
    } <= selected


def test_category_domain_announces_full_category_rubric_toolset():
    """Cualquier turno de categorías/rúbricas anuncia las herramientas del prompt."""
    selected = set(select_tool_names({}, [{"role": "user", "content": "configura las categorías"}]))

    assert {
        "getSiteFairs",
        "listFairCategories",
        "getFairRubric",
        "createFairCategory",
        "setup_category_with_rubric",
    } <= selected
