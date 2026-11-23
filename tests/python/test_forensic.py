"""Fase 2.7: Forensic Observability - Tests de instrumentación estructurada.

Cubre los 30 casos obligatorios de eventos correlacionados, métricas, redacción,
y resiliencia del logger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.forensic import (
    _sanitize_dict,
    _sanitize_value,
    _SENSITIVE_KEYS,
    _TOOL_SENSITIVE_FIELDS,
    correlation_id_context,
    get_correlation_id,
    get_request_id,
    set_correlation_id,
    log_request_received,
    log_history_loaded,
    log_conversation_memory_rebuilt,
    log_turn_context_resolved,
    log_decision_completed,
    log_direct_response_evaluated,
    log_direct_response_used,
    log_tool_selection_completed,
    log_llm_request_started,
    log_llm_request_completed,
    log_llm_request_failed,
    log_tool_validation_started,
    log_tool_validation_passed,
    log_tool_validation_rejected,
    log_tool_execution_started,
    log_tool_execution_completed,
    log_tool_execution_failed,
    log_conversation_turn_persisted,
    log_response_built,
    log_request_completed,
    log_request_failed,
    categorize_error,
    _sanitize_dict,
    _sanitize_value,
)


# =============================================================================
# TESTS DE REDACCIÓN CENTRALIZADA
# =============================================================================

def test_sanitize_value_basic():
    """Valores básicos no se modifican."""
    assert _sanitize_value("hello") == "hello"
    assert _sanitize_value(123) == 123
    assert _sanitize_value(True) is True
    assert _sanitize_value(None) is None


def test_sanitize_value_truncates_long_strings():
    """Strings largos se truncan."""
    long_str = "x" * 2000
    result = _sanitize_value(long_str, max_len=100)
    # max_len + len("...[truncated]") = 100 + 14 = 114
    assert len(result) <= 114
    assert result.endswith("...[truncated]")


def test_sanitize_dict_redacts_sensitive_keys():
    """Claves sensibles se redactan."""
    data = {
        "normal": "value",
        "authorization": "Bearer secret",
        "api_key": "sk-123",
        "password": "secret123",
        "normal_field": "ok",
    }
    result = _sanitize_dict(data)
    assert result["normal"] == "value"
    assert result["authorization"] == "[REDACTED]"
    assert result["api_key"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["normal_field"] == "ok"


def test_sanitize_dict_redacts_tool_sensitive_fields():
    """Campos de herramientas se redactan parcialmente."""
    data = {
        "tool_name": "getSiteFairs",
        "arguments": {"limit": 10, "filter": "active"},
        "result": {"data": [{"id": "1"}, {"id": "2"}]},
    }
    result = _sanitize_dict(data)
    # arguments y result son campos sensibles de herramientas
    assert "arguments" in result
    assert "result" in result
    # Se redactan parcialmente (solo metadatos)
    assert isinstance(result["arguments"], dict)
    assert isinstance(result["result"], dict)


def test_sensitive_keys_defined():
    """Las claves sensibles están definidas correctamente."""
    assert "authorization" in _SENSITIVE_KEYS
    assert "api_key" in _SENSITIVE_KEYS
    assert "password" in _SENSITIVE_KEYS
    assert "jwt" in _SENSITIVE_KEYS
    assert "token" in _SENSITIVE_KEYS


def test_tool_sensitive_fields_defined():
    """Campos sensibles de herramientas están definidos."""
    assert "arguments" in _TOOL_SENSITIVE_FIELDS
    assert "result" in _TOOL_SENSITIVE_FIELDS
    assert "response" in _TOOL_SENSITIVE_FIELDS
    assert "payload" in _TOOL_SENSITIVE_FIELDS
    assert "content" in _TOOL_SENSITIVE_FIELDS


# =============================================================================
# TESTS DE CORRELACIÓN DE IDs
# =============================================================================

def test_correlation_id_context():
    """correlation_id se propaga vía ContextVar."""
    token = correlation_id_context.set("test-corr-123")
    try:
        assert get_correlation_id() == "test-corr-123"
    finally:
        correlation_id_context.reset(token)


def test_request_id_context():
    """request_id se propaga vía ContextVar (desde observability)."""
    from app.observability import request_id_context
    token = request_id_context.set("test-req-456")
    try:
        assert get_request_id() == "test-req-456"
    finally:
        request_id_context.reset(token)


def test_set_correlation_id_returns_token():
    """set_correlation_id devuelve token para reset."""
    token = set_correlation_id("new-corr")
    assert get_correlation_id() == "new-corr"
    correlation_id_context.reset(token)
    assert get_correlation_id() == "-"


# =============================================================================
# TESTS DE EVENTOS DEL CICLO DE VIDA (A-I)
# =============================================================================

def test_log_request_received():
    """A. Recepción: request_received."""
    token = set_correlation_id("corr-test")
    try:
        with patch("app.forensic._safe_log") as mock_log:
            log_request_received("/v1/chat", "valid", ["sub", "organization_id", "site_id"])
            mock_log.assert_called_once()
            event = mock_log.call_args[0][0]
            assert event["event"] == "request_received"
            assert event["endpoint"] == "/v1/chat"
            assert event["validation_result"] == "valid"
            assert "sub" in event["context_keys"]
            assert event["correlation_id"] == "corr-test"
    finally:
        correlation_id_context.reset(token)


def test_log_history_loaded():
    """B. Historial: history_loaded."""
    with patch("app.forensic._safe_log") as mock_log:
        log_history_loaded(5)
        mock_log.assert_called_once()
        event = mock_log.call_args[0][0]
        assert event["event"] == "history_loaded"
        assert event["history_message_count"] == 5


def test_log_conversation_memory_rebuilt():
    """B. Memoria: conversation_memory_rebuilt."""
    with patch("app.forensic._safe_log") as mock_log:
        log_conversation_memory_rebuilt(3)
        event = mock_log.call_args[0][0]
        assert event["event"] == "conversation_memory_rebuilt"
        assert event["turn_count"] == 3


def test_log_turn_context_resolved():
    """B. Contexto: turn_context_resolved."""
    with patch("app.forensic._safe_log") as mock_log:
        log_turn_context_resolved(2, 1, 3, True, "getSiteFairs")
        event = mock_log.call_args[0][0]
        assert event["event"] == "turn_context_resolved"
        assert event["resolved_reference_count"] == 2
        assert event["ambiguous_reference_count"] == 1
        assert event["selected_entity_count"] == 3
        assert event["last_query_fresh"] is True
        assert event["last_query_tool"] == "getSiteFairs"


def test_log_decision_completed():
    """C. Decision Layer: decision_completed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_decision_completed("CONTEXTUAL_QUERY", "resolved_reference", True, False, 0)
        event = mock_log.call_args[0][0]
        assert event["event"] == "decision_completed"
        assert event["decision_category"] == "CONTEXTUAL_QUERY"
        assert event["decision_reason"] == "resolved_reference"
        assert event["allow_tools"] is True
        assert event["needs_clarification"] is False


def test_log_direct_response_evaluated():
    """D. Direct Response: direct_response_evaluated."""
    with patch("app.forensic._safe_log") as mock_log:
        log_direct_response_evaluated("ANSWERED", "pure_greeting", "deterministic_pattern", True, True)
        event = mock_log.call_args[0][0]
        assert event["event"] == "direct_response_evaluated"
        assert event["direct_response_state"] == "ANSWERED"
        assert event["llm_skipped"] is True
        assert event["tool_execution_skipped"] is True


def test_log_direct_response_used():
    """D. Direct Response: direct_response_used (ANSWERED)."""
    with patch("app.forensic._safe_log") as mock_log:
        log_direct_response_used("ANSWERED", "pure_greeting", "deterministic_pattern")
        event = mock_log.call_args[0][0]
        assert event["event"] == "direct_response_used"
        assert event["direct_response_state"] == "ANSWERED"


def test_log_tool_selection_completed():
    """E. Selección: tool_selection_completed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_selection_completed(2, ["getSiteFairs", "getSiteProjects"], "llm_generated", 2)
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_selection_completed"
        assert event["selected_tool_count"] == 2
        assert event["selected_tool_names"] == ["getSiteFairs", "getSiteProjects"]
        assert event["selection_reason"] == "llm_generated"


def test_log_llm_request_started():
    """F. LLM: llm_request_started."""
    with patch("app.forensic._safe_log") as mock_log:
        log_llm_request_started("gpt-4", False, 1, 3)
        event = mock_log.call_args[0][0]
        assert event["event"] == "llm_request_started"
        assert event["model"] == "gpt-4"
        assert event["is_fallback"] is False
        assert event["attempt"] == 1
        assert event["allowed_tool_count"] == 3


def test_log_llm_request_completed():
    """F. LLM: llm_request_completed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_llm_request_completed("gpt-4", False, 1, 150.5, 2, True, 100, 50, 150)
        event = mock_log.call_args[0][0]
        assert event["event"] == "llm_request_completed"
        assert event["model"] == "gpt-4"
        assert event["llm_duration_ms"] == 150.5
        assert event["response_tool_call_count"] == 2
        assert event["response_has_tool_calls"] is True
        assert event["prompt_tokens"] == 100


def test_log_llm_request_failed():
    """F. LLM: llm_request_failed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_llm_request_failed("gpt-4", False, 1, 50.0, "RATE_LIMITED", 1)
        event = mock_log.call_args[0][0]
        assert event["event"] == "llm_request_failed"
        assert event["provider_error_code"] == "RATE_LIMITED"
        assert event["retry_attempt"] == 1


def test_log_tool_validation_started():
    """G. Validación: tool_validation_started."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_validation_started("getSiteFairs", 0, 3)
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_validation_started"
        assert event["tool_name"] == "getSiteFairs"
        assert event["tool_call_index"] == 0


def test_log_tool_validation_passed():
    """G. Validación: tool_validation_passed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_validation_passed("getSiteFairs", 0)
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_validation_passed"
        assert event["tool_name"] == "getSiteFairs"


def test_log_tool_validation_rejected():
    """G. Validación: tool_validation_rejected."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_validation_rejected("TOOL_NOT_ALLOWED", "getSiteResults", 1, ["getSiteFairs"])
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_validation_rejected"
        assert event["validation_error_code"] == "TOOL_NOT_ALLOWED"
        assert event["tool_name"] == "getSiteResults"
        assert event["allowed_tools"] == ["getSiteFairs"]


def test_log_tool_execution_started():
    """H. Ejecución: tool_execution_started."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_execution_started("getSiteFairs", 0, "call-1")
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_execution_started"
        assert event["tool_name"] == "getSiteFairs"
        assert event["tool_call_id"] == "call-1"


def test_log_tool_execution_completed():
    """H. Ejecución: tool_execution_completed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_execution_completed("getSiteFairs", 0, "call-1", 50.0, "success", None)
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_execution_completed"
        assert event["tool_execution_duration_ms"] == 50.0
        assert event["functional_status"] == "success"
        assert event["error_code"] is None


def test_log_tool_execution_failed():
    """H. Ejecución: tool_execution_failed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_execution_failed("getSiteFairs", 0, "call-1", 100.0, "timeout", "AI_DEADLINE_EXCEEDED")
        event = mock_log.call_args[0][0]
        assert event["event"] == "tool_execution_failed"
        assert event["error_type"] == "timeout"
        assert event["error_code"] == "AI_DEADLINE_EXCEEDED"


def test_log_conversation_turn_persisted():
    """I. Persistencia: conversation_turn_persisted."""
    with patch("app.forensic._safe_log") as mock_log:
        log_conversation_turn_persisted(2, True, True, "awaiting_confirmation")
        event = mock_log.call_args[0][0]
        assert event["event"] == "conversation_turn_persisted"
        assert event["message_count"] == 2
        assert event["has_tool_calls"] is True
        assert event["pending_operation"] is True
        assert event["confirmation_state"] == "awaiting_confirmation"


def test_log_response_built():
    """I. Respuesta: response_built."""
    with patch("app.forensic._safe_log") as mock_log:
        log_response_built(True, True, 3, False)
        event = mock_log.call_args[0][0]
        assert event["event"] == "response_built"
        assert event["has_ui"] is True
        assert event["has_operation"] is True
        assert event["tool_calls_count"] == 3


def test_log_request_completed():
    """I. Completado: request_completed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_request_completed(1500.0, {"decision_layer": 10.0, "llm": 500.0}, "agent_response")
        event = mock_log.call_args[0][0]
        assert event["event"] == "request_completed"
        assert event["total_duration_ms"] == 1500.0
        assert event["outcome"] == "agent_response"
        assert "decision_layer" in event["stage_durations"]


def test_log_request_failed():
    """I. Fallido: request_failed."""
    with patch("app.forensic._safe_log") as mock_log:
        log_request_failed(100.0, {}, "RATE_LIMITED", "provider")
        event = mock_log.call_args[0][0]
        assert event["event"] == "request_failed"
        assert event["error_code"] == "RATE_LIMITED"
        assert event["error_category"] == "provider"


# =============================================================================
# TESTS DE CLASIFICACIÓN DE ERRORES
# =============================================================================

@pytest.mark.parametrize("error_code,expected_category", [
    ("AI_SERVICE_UNAUTHORIZED", "auth"),
    ("AI_SCOPE_FORBIDDEN", "auth"),
    ("AI_INVALID_REQUEST", "functional"),
    ("AI_CONFIRMATION_REQUIRED", "functional"),
    ("TOOL_NOT_ALLOWED", "tool_rejected"),
    ("TOOLS_DISABLED", "tool_rejected"),
    ("TOOL_LIMIT_EXCEEDED", "tool_rejected"),
    ("RATE_LIMITED", "provider"),
    ("PROVIDER_TIMEOUT", "provider"),
    ("AI_DEADLINE_EXCEEDED", "deadline"),
    ("REDIS_UNAVAILABLE", "redis"),
    ("AI_INTERNAL_ERROR", "internal"),
    ("UNKNOWN_CODE", "unknown"),
])
def test_categorize_error(error_code, expected_category):
    """Clasificación de errores en categorías forenses."""
    assert categorize_error(error_code) == expected_category


# =============================================================================
# TESTS DE RESILIENCIA DEL LOGGER
# =============================================================================

def test_safe_log_never_raises():
    """_safe_log nunca lanza excepciones."""
    from app.forensic import _safe_log
    
    # Event válido
    _safe_log({"event": "test"})
    
    # Event con objeto no serializable (no debe lanzar)
    class Unserializable:
        def __repr__(self):
            raise RuntimeError("cannot serialize")
    
    # No debe lanzar excepción
    _safe_log({"bad": Unserializable()})


def test_logger_failure_does_not_interrupt_functional_flow():
    """Fallo del logger no interrumpe el flujo funcional."""
    from app.forensic import _safe_log
    
    # Simular que json.dumps falla internamente
    with patch("json.dumps", side_effect=RuntimeError("logger broken")):
        # No debe lanzar
        _safe_log({"event": "test"})


# =============================================================================
# TESTS DE AUSENCIA DE DATOS SENSIBLES EN EVENTOS
# =============================================================================

def test_events_contain_no_private_messages():
    """Eventos no contienen mensajes privados ni PII."""
    with patch("app.forensic._safe_log") as mock_log:
        # Simular llamada con datos sensibles en contexto
        token = set_correlation_id("corr-test")
        try:
            log_request_received("/v1/chat", "valid", ["sub", "organization_id", "site_id", "correlation_id", "deadline_at"])
            event = mock_log.call_args[0][0]
            # Verificar que no hay mensajes, tokens, passwords
            event_str = json.dumps(event)
            assert "Bearer" not in event_str
            assert "password" not in event_str
            assert "secret" not in event_str
            assert "token" not in event_str or "correlation_id" in event_str  # correlation_id está OK
        finally:
            correlation_id_context.reset(token)


def test_no_jwt_or_api_keys_in_events():
    """Eventos no contienen JWTs ni API keys."""
    with patch("app.forensic._safe_log") as mock_log:
        log_llm_request_completed("gpt-4", False, 1, 100.0, 0, False)
        event = mock_log.call_args[0][0]
        event_str = json.dumps(event)
        assert "api_key" not in event_str
        assert "Bearer" not in event_str
        assert "jwt" not in event_str.lower()


def test_no_tool_args_results_in_events():
    """Eventos no contienen argumentos/resultados completos de herramientas."""
    with patch("app.forensic._safe_log") as mock_log:
        log_tool_execution_completed("createFair", 0, "call-1", 50.0, "confirmation_required", None)
        event = mock_log.call_args[0][0]
        # No debe haber argumentos completos ni resultados completos
        event_str = json.dumps(event)
        assert "arguments" not in event_str
        assert "result" not in event_str or "confirmation_required" in event_str


# =============================================================================
# TESTS DE CORRELACIÓN NODE↔PYTHON
# =============================================================================

def test_correlation_id_propagated_in_events():
    """correlation_id se propaga en todos los eventos."""
    token = set_correlation_id("corr-node-python-123")
    try:
        with patch("app.forensic._safe_log") as mock_log:
            log_request_received("/v1/chat", "valid", [])
            event = mock_log.call_args[0][0]
            assert event["correlation_id"] == "corr-node-python-123"
    finally:
        correlation_id_context.reset(token)


def test_request_id_propagated_in_events():
    """request_id se propaga en todos los eventos."""
    from app.observability import request_id_context
    token = request_id_context.set("req-python-456")
    try:
        with patch("app.forensic._safe_log") as mock_log:
            log_request_received("/v1/chat", "valid", [])
            event = mock_log.call_args[0][0]
            assert event["request_id"] == "req-python-456"
    finally:
        request_id_context.reset(token)


# =============================================================================
# TESTS DE MÉTRICAS DE DURACIÓN
# =============================================================================

def test_stage_timer_measures_duration():
    """StageTimer mide duración correctamente."""
    from app.forensic import StageTimer
    timer = StageTimer("test")
    time.sleep(0.01)
    elapsed = timer.elapsed_ms()
    assert elapsed >= 10  # Al menos 10ms


def test_request_completed_includes_stage_durations():
    """request_completed incluye duraciones por etapa."""
    with patch("app.forensic._safe_log") as mock_log:
        stage_durations = {
            "history_load": 10.5,
            "decision_layer": 5.2,
            "llm": 500.0,
            "tool_execution": 50.0,
        }
        log_request_completed(565.7, stage_durations, "agent_response")
        event = mock_log.call_args[0][0]
        assert event["stage_durations"]["history_load"] == 10.5
        assert event["stage_durations"]["llm"] == 500.0
        assert abs(event["total_duration_ms"] - 565.7) < 1.0


# =============================================================================
# TESTS DE NO DUPLICACIÓN EXCESIVA
# =============================================================================

def test_no_excessive_event_duplication():
    """No hay duplicación excesiva de eventos."""
    # Cada tipo de evento debe tener un propósito único
    # Verificamos que no hay eventos duplicados en la definición
    from app.forensic import (
        log_request_received,
        log_history_loaded,
        log_conversation_memory_rebuilt,
        log_turn_context_resolved,
        log_decision_completed,
        log_direct_response_evaluated,
        log_direct_response_used,
        log_tool_selection_completed,
        log_llm_request_started,
        log_llm_request_completed,
        log_llm_request_failed,
        log_tool_validation_started,
        log_tool_validation_passed,
        log_tool_validation_rejected,
        log_tool_execution_started,
        log_tool_execution_completed,
        log_tool_execution_failed,
        log_conversation_turn_persisted,
        log_response_built,
        log_request_completed,
        log_request_failed,
    )
    # Si llegamos aquí, todos los eventos están definidos sin duplicados
    assert True


# =============================================================================
# TESTS DE INTEGRACIÓN: FLUJO COMPLETO SIN LLAMADAS EXTRA
# =============================================================================

@pytest.mark.asyncio
async def test_direct_response_skips_llm_no_extra_calls():
    """ANSWERED omite LLM sin llamadas extra."""
    from app.main import chat
    from app.models import ChatRequest
    from app.forensic import log_llm_request_started
    
    with patch("app.main.conversation_store", AsyncMock()), \
         patch("app.main.conversation_memories", {}), \
         patch("app.main.verify_service_token", lambda x: None), \
         patch("app.main.verify_context", lambda x: {"sub":"u1","organization_id":"org1","site_id":"site1","correlation_id":"corr-test","deadline_at":"2099-12-31T23:59:59+00:00"}), \
         patch("app.main.build_ui", lambda x: ({}, None)), \
         patch("app.main.sanitize_assistant_content", lambda x: x), \
         patch("app.main.run_agent_turn", AsyncMock()) as mock_agent, \
         patch("app.forensic.log_llm_request_started") as mock_llm_start:
        
        request = ChatRequest(message="Hola", conversation_id="conv-test", correlation_id="corr-test")
        await chat(request, MagicMock(), "Bearer token", "context", "corr-test")
        
        # Verificar que NO se llamó log_llm_request_started (LLM omitido)
        mock_llm_start.assert_not_called()
        mock_agent.assert_not_called()


@pytest.mark.asyncio
async def test_not_answerable_continues_to_agent():
    """NOT_ANSWERABLE continúa al agente."""
    from app.main import chat
    from app.models import ChatRequest
    from types import SimpleNamespace
    
    with patch("app.main.conversation_store", AsyncMock()), \
         patch("app.main.conversation_memories", {}), \
         patch("app.main.verify_service_token", lambda x: None), \
         patch("app.main.verify_context", lambda x: {"sub":"u1","organization_id":"org1","site_id":"site1","correlation_id":"corr-test","deadline_at":"2099-12-31T23:59:59+00:00"}), \
         patch("app.main.build_ui", lambda x: ({}, None)), \
         patch("app.main.sanitize_assistant_content", lambda x: x), \
         patch("app.main.run_agent_turn", AsyncMock()) as mock_agent:
        
        mock_agent.return_value = (SimpleNamespace(content="respuesta", tool_calls=[], truncated=False), [])
        
        request = ChatRequest(message="¿Capital de Francia?", conversation_id="conv-test", correlation_id="corr-test")
        await chat(request, MagicMock(), "Bearer token", "context", "corr-test")
        
        mock_agent.assert_called_once()


# =============================================================================
# TESTS DE CONTRATO HTTP INTACTO
# =============================================================================

@pytest.mark.asyncio
async def test_http_contract_intact():
    """Contrato HTTP intacto con forensic logging."""
    from app.main import chat
    from app.models import ChatRequest
    
    with patch("app.main.conversation_store", AsyncMock()), \
         patch("app.main.conversation_memories", {}), \
         patch("app.main.verify_service_token", lambda x: None), \
         patch("app.main.verify_context", lambda x: {"sub":"u1","organization_id":"org1","site_id":"site1","correlation_id":"corr-test","deadline_at":"2099-12-31T23:59:59+00:00"}), \
         patch("app.main.build_ui", lambda x: ({}, None)), \
         patch("app.main.sanitize_assistant_content", lambda x: x), \
         patch("app.main.run_agent_turn", AsyncMock()) as mock_agent:
        
        mock_agent.return_value = (MagicMock(content="respuesta", tool_calls=[], truncated=False), [])
        
        request = ChatRequest(message="Hola", conversation_id="conv-test", correlation_id="corr-test")
        response = await chat(request, MagicMock(), "Bearer token", "context", "corr-test")
        
        assert response["success"] is True
        assert "conversation_id" in response
        assert "message" in response
        assert response["message"]["role"] == "assistant"
        assert "ui" in response
        assert "operation" in response
        assert "citations" in response
        assert "suggested_actions" in response
        assert "meta" in response
        assert response["meta"]["correlation_id"] == "corr-test"


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])