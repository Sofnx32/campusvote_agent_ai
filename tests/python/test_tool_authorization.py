"""Regresiones permanentes 3.3: autorización de herramientas hasta la ejecución.

La pertenencia a ``TOOLS`` prueba existencia, no autorización. Estas pruebas
verifican que el conjunto anunciado por el selector se conserva hasta el punto
efectivo de ejecución, también con respuesta de fallback y con varias llamadas.
"""

from __future__ import annotations

import json

import pytest

from app.agent import run_agent_turn
from app.models import LLMResult
from app.providers.tool_registry import TOOLS
from app.providers.tool_selector import select_tool_names
from provider_helpers import (
    MockResponse, content_response, make_provider, run, tool_response,
)

CONTEXT = {"site_id": "site-a"}
FERIAS = "Muestra las ferias de mi sede"
FALLBACK = {
    "llm_fallback_url": "https://fallback.example/v1/chat/completions",
    "llm_fallback_model": "fallback-model",
    "llm_fallback_api_key": "fallback-key",
}


def spy(executed: list[str]):
    async def execute(tool_call, context, message, **kwargs):
        executed.append(tool_call.name)
        return {"success": True, "tool": tool_call.name}
    return execute


def multiple_response(calls: list[tuple[str, dict]]) -> MockResponse:
    body = {"choices": [{"message": {
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}
            for call_id, name, arguments in calls
        ],
    }}]}
    return MockResponse(payload=body)


class RecordingProvider:
    """Delega en el proveedor real y guarda los LLMResult devueltos."""

    def __init__(self, inner):
        self.inner = inner
        self.results: list[LLMResult] = []

    async def complete(self, context, messages, allow_tools=True):
        result = await self.inner.complete(context, messages, allow_tools)
        self.results.append(result)
        return result


def test_case_a_general_conversation_does_not_execute_unexpected_tool(monkeypatch):
    """A) 'hola' -> selector [], sin schemas; tool_call inesperado: 0 ejecuciones."""
    executed, captured = [], []
    Provider, AgentError = make_provider(
        monkeypatch, response=tool_response("getSiteFairs", {}), captured=captured,
    )
    assert select_tool_names(CONTEXT, [{"role": "user", "content": "hola"}]) == []

    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            Provider(), CONTEXT, "hola", [], spy(executed),
            max_tool_calls=5, deadline_seconds=5,
        ))
    assert error.value.code == "AI_TOOL_NOT_FOUND"
    assert executed == []
    assert "tools" not in captured[0]["json"]
    assert error.value.details["reason"] == "TOOL_NOT_AUTHORIZED"


def test_case_b_allow_tools_false_does_not_execute_unexpected_tool(monkeypatch):
    """B) allow_tools=False en la segunda ronda: 0 ejecuciones no autorizadas."""
    executed, captured = [], []
    Provider, AgentError = make_provider(
        monkeypatch,
        responses=[tool_response("getSiteFairs", {}), tool_response("getSiteResults", {})],
        captured=captured,
    )
    result, results = run(run_agent_turn(
        Provider(), CONTEXT, FERIAS, [], spy(executed),
        max_tool_calls=1, deadline_seconds=5,
    ))
    assert executed == ["getSiteFairs"]
    assert "getSiteResults" not in executed  # 0 ejecuciones de la llamada inesperada
    assert len(results) == 1
    assert "tools" in captured[0]["json"]
    assert "tools" not in captured[1]["json"]  # allow_tools=False
    assert [call.name for call in result.tool_calls] == ["getSiteResults"]


def test_case_c_authorized_subset_tool_is_executed(monkeypatch):
    """C) Herramienta perteneciente al subconjunto: se autoriza y ejecuta."""
    executed, captured = [], []
    Provider, _ = make_provider(
        monkeypatch,
        responses=[tool_response("getSiteFairs", {}), content_response("Listo")],
        captured=captured,
    )
    result, results = run(run_agent_turn(
        Provider(), CONTEXT, FERIAS, [], spy(executed),
        max_tool_calls=5, deadline_seconds=5,
    ))
    assert executed == ["getSiteFairs"]
    assert len(results) == 1
    assert result.content == "Listo"
    announced = [schema["function"]["name"] for schema in captured[0]["json"]["tools"]]
    assert "getSiteFairs" in announced


def test_case_d_registered_tool_outside_subset_is_rejected(monkeypatch):
    """D) Registrada en TOOLS pero fuera del subconjunto: rechazo y 0 ejecuciones."""
    executed, captured = [], []
    Provider, AgentError = make_provider(
        monkeypatch, response=tool_response("getSiteResults", {}), captured=captured,
    )
    authorized = select_tool_names(CONTEXT, [{"role": "user", "content": FERIAS}])
    assert "getSiteResults" in TOOLS
    assert "getSiteResults" not in authorized

    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            Provider(), CONTEXT, FERIAS, [], spy(executed),
            max_tool_calls=5, deadline_seconds=5,
        ))
    assert error.value.code == "AI_TOOL_NOT_FOUND"
    assert executed == []
    assert error.value.details["tool"] == "getSiteResults"


def test_case_e_unknown_name_is_rejected_before_execution(monkeypatch):
    """E) Nombre desconocido: AI_TOOL_NOT_FOUND y 0 ejecuciones."""
    executed = []
    Provider, AgentError = make_provider(
        monkeypatch, response=tool_response("toolInexistente", {}),
    )
    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            Provider(), CONTEXT, FERIAS, [], spy(executed),
            max_tool_calls=5, deadline_seconds=5,
        ))
    assert error.value.code == "AI_TOOL_NOT_FOUND"
    assert executed == []


def test_case_f_invalid_arguments_are_rejected_before_execution(monkeypatch):
    """F) JSON inválido en herramienta autorizada: error estructurado, 0 ejecuciones."""
    executed = []
    response = MockResponse(payload={"choices": [{"message": {
        "content": None,
        "tool_calls": [{"id": "bad-1", "function": {
            "name": "getSiteFairs", "arguments": "{no-json",
        }}],
    }}]})
    Provider, AgentError = make_provider(monkeypatch, response=response)
    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            Provider(), CONTEXT, FERIAS, [], spy(executed),
            max_tool_calls=5, deadline_seconds=5,
        ))
    assert error.value.code == "AI_RESPONSE_INVALID"
    assert error.value.details["reason"] == "INVALID_TOOL_ARGUMENTS_JSON"
    assert executed == []


def test_case_g_mixed_calls_preserve_ids_without_unauthorized_execution(monkeypatch):
    """G) Mezcla autorizada/no autorizada: 0 no autorizadas, IDs intactos."""
    executed, captured = [], []
    Provider, AgentError = make_provider(
        monkeypatch,
        response=multiple_response([
            ("ok-1", "getSiteFairs", {}),
            ("bad-1", "getSiteResults", {}),
        ]),
        captured=captured,
    )
    recorder = RecordingProvider(Provider())
    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            recorder, CONTEXT, FERIAS, [], spy(executed),
            max_tool_calls=5, deadline_seconds=5,
        ))
    assert error.value.code == "AI_TOOL_NOT_FOUND"
    assert executed == []
    # El parser conservó todas las llamadas y sus IDs antes del rechazo.
    assert [call.id for call in recorder.results[0].tool_calls] == ["ok-1", "bad-1"]
    assert [call.name for call in recorder.results[0].tool_calls] == [
        "getSiteFairs", "getSiteResults",
    ]


def test_case_h_fallback_applies_the_same_authorization(monkeypatch):
    """H) Fallback con tool_calls: misma autorización y 0 ejecuciones fuera del conjunto."""
    executed, captured = [], []
    Provider, AgentError = make_provider(
        monkeypatch,
        responses=[MockResponse(status=503, payload={}), tool_response("getSiteResults", {})],
        captured=captured, overrides=FALLBACK,
    )
    with pytest.raises(AgentError) as error:
        run(run_agent_turn(
            Provider(), CONTEXT, FERIAS, [], spy(executed),
            max_tool_calls=5, deadline_seconds=5,
        ))
    assert error.value.code == "AI_TOOL_NOT_FOUND"
    assert executed == []
    assert len(captured) == 2  # primario + fallback
    assert len(captured[1]["url"]) > 0
    assert "getSiteResults" not in [
        schema["function"]["name"] for schema in captured[1]["json"].get("tools", [])
    ]


def test_case_i_authorized_write_executes_once_without_retry(monkeypatch):
    """I) Escritura autorizada: una sola ejecución, sin reintento de herramienta."""
    executed, captured = [], []
    Provider, _ = make_provider(
        monkeypatch,
        responses=[tool_response("createFair", {"name": "Feria"}), content_response("Listo")],
        captured=captured,
    )
    run(run_agent_turn(
        Provider(), CONTEXT, FERIAS, [], spy(executed),
        max_tool_calls=5, deadline_seconds=5,
    ))
    assert executed == ["createFair"]
    assert len(captured) == 2  # dos llamadas LLM; la herramienta no se re-ejecutó
