"""Validación estricta de deadlines/presupuestos y acotamiento de `deadline_at`."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def _config():
    """Importación diferida: no cargar `app.config` durante la recolección.

    `test_context.py` fija AI_SERVICE_TOKEN al importarse; cargar config antes
    rompería ese contrato de pruebas. Aquí se importa solo cuando el test corre.
    """
    from app import config
    return config


def at(offset_seconds: float) -> str:
    return (NOW + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Configuración aceptada
# ---------------------------------------------------------------------------

def test_default_configuration_is_accepted():
    cfg = _config()
    assert cfg.validate_configuration(cfg.settings) is None


def test_boundary_configuration_is_accepted():
    cfg = _config()
    config = replace(
        cfg.settings,
        agent_deadline_seconds=20,
        llm_total_budget_seconds=19.5,
        llm_fallback_budget_seconds=1.0,
        llm_retry_min_next_attempt_seconds=0.5,
        tool_timeout_seconds=19.5,
    )
    assert cfg.validate_configuration(config) is None


def test_startup_lifespan_accepts_valid_configuration():
    from app.main import app, lifespan

    async def run():
        async with lifespan(app):
            return True

    assert asyncio.run(run()) is True


# ---------------------------------------------------------------------------
# Configuración rechazada: valores no positivos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field",
    [
        "agent_deadline_seconds",
        "llm_timeout_seconds",
        "llm_total_budget_seconds",
        "llm_fallback_budget_seconds",
        "llm_retry_base_delay_seconds",
        "llm_retry_max_delay_seconds",
        "llm_retry_min_next_attempt_seconds",
        "tool_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_timings_are_rejected(field, value):
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match=f"{field} must be > 0"):
        cfg.validate_configuration(replace(cfg.settings, **{field: value}))


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_node_timeout_is_rejected(monkeypatch, value):
    cfg = _config()
    monkeypatch.setenv("AI_CHAT_TIMEOUT_MS", str(value * 1000))
    with pytest.raises(cfg.ConfigurationError, match="node_http_timeout must be > 0"):
        cfg.validate_configuration(cfg.settings)


# ---------------------------------------------------------------------------
# Configuración rechazada: contradicciones entre presupuestos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("total,fallback", [(30, 5), (30, 30), (29, 30)])
def test_llm_budget_hierarchy_is_rejected(total, fallback):
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError):
        cfg.validate_configuration(replace(
            cfg.settings,
            llm_total_budget_seconds=total,
            llm_fallback_budget_seconds=fallback,
        ))


def test_llm_budget_above_agent_deadline_is_rejected():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match="llm_total_budget must not exceed"):
        cfg.validate_configuration(replace(cfg.settings, llm_total_budget_seconds=30))


def test_primary_remaining_below_min_next_attempt_is_rejected():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match="llm_total_budget minus llm_fallback_budget"):
        cfg.validate_configuration(replace(
            cfg.settings,
            llm_total_budget_seconds=5.4,
            llm_fallback_budget_seconds=5.0,
        ))


def test_fallback_budget_below_min_next_attempt_is_rejected():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match="llm_fallback_budget must be >="):
        cfg.validate_configuration(replace(cfg.settings, llm_fallback_budget_seconds=0.2))


def test_tool_timeout_above_agent_deadline_is_rejected():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match="tool_timeout must not exceed"):
        cfg.validate_configuration(replace(cfg.settings, tool_timeout_seconds=30))


def test_llm_budget_plus_reserve_above_agent_deadline_is_rejected():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match="llm_total_budget plus"):
        cfg.validate_configuration(replace(
            cfg.settings,
            llm_total_budget_seconds=28,
            llm_fallback_budget_seconds=1.0,
        ))


def test_node_and_jwt_chain_is_rejected(monkeypatch):
    cfg = _config()
    monkeypatch.setenv("AI_CHAT_TIMEOUT_MS", "10000")
    monkeypatch.setenv("AI_CONTEXT_TTL_SECONDS", "5")
    with pytest.raises(cfg.ConfigurationError, match="node_http_timeout must be lower"):
        cfg.validate_configuration(cfg.settings)


def test_error_message_lists_every_violation():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError) as error:
        cfg.validate_configuration(replace(
            cfg.settings,
            agent_deadline_seconds=0,
            llm_total_budget_seconds=0,
            tool_timeout_seconds=0,
        ))
    message = str(error.value)
    assert "agent_deadline_seconds must be > 0" in message
    assert "llm_total_budget_seconds must be > 0" in message
    assert "tool_timeout_seconds must be > 0" in message
    assert message.startswith("Invalid deadline/budget configuration:")


# ---------------------------------------------------------------------------
# Presupuesto efectivo en runtime (min(configurado, deadline_at restante))
# ---------------------------------------------------------------------------

def test_effective_budget_keeps_configured_when_deadline_is_far():
    cfg = _config()
    assert cfg.effective_budget_seconds(at(60), 20, now=NOW) == 20


def test_effective_budget_clamps_to_remaining_deadline():
    cfg = _config()
    assert cfg.effective_budget_seconds(at(3), 20, now=NOW) == pytest.approx(3)


def test_effective_budget_uses_default_when_not_configured():
    cfg = _config()
    assert cfg.effective_budget_seconds(at(60), now=NOW) == cfg.settings.agent_deadline_seconds


def test_effective_budget_expired_deadline_fails_fast():
    cfg = _config()
    with pytest.raises(cfg.AgentError) as error:
        cfg.effective_budget_seconds(at(-1), 20, now=NOW)
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert error.value.status == 504
    assert error.value.details["remaining_seconds"] == -1


@pytest.mark.parametrize("value", ["not-a-date", "", None])
def test_effective_budget_invalid_deadline_fails_fast(value):
    cfg = _config()
    with pytest.raises(cfg.AgentError) as error:
        cfg.effective_budget_seconds(value, 20, now=NOW)
    assert error.value.code == "AI_DEADLINE_EXCEEDED"
    assert error.value.details["reason"] == "INVALID_DEADLINE_AT"


def test_effective_budget_rejects_non_positive_configuration():
    cfg = _config()
    with pytest.raises(cfg.ConfigurationError, match="configured_seconds must be > 0"):
        cfg.effective_budget_seconds(at(60), 0, now=NOW)
