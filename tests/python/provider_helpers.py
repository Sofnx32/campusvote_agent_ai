"""Helpers de pruebas del proveedor (sin estado de clase entre tests)."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import logging

import httpx

from app.config import settings as real_settings

SETTINGS_MODULES = (
    "app.providers.openai_compatible",
    "app.providers.http_transport",
    "app.providers.response_parser",
    "app.tools",
    "app.main",
)

DEFAULTS = {
    "llm_provider": "openai-compatible",
    "llm_url": "https://llm.example/v1/chat/completions",
    "llm_model": "model-x",
    "llm_api_key": "secret-test-key",
    "llm_fallback_url": "",
    "llm_fallback_model": "",
    "llm_fallback_api_key": "",
    "llm_timeout_seconds": 2.0,
    "llm_total_budget_seconds": 5.0,
    "llm_fallback_budget_seconds": 1.0,
    "llm_max_output_tokens": 2000,
    "llm_output_token_param": "max_tokens",
    "llm_max_retries": 0,
    "llm_retry_base_delay_seconds": 0.01,
    "llm_retry_max_delay_seconds": 0.02,
    "llm_retry_min_next_attempt_seconds": 0.001,
}


class MockResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.payload = payload if payload is not None else {"choices": [{"message": {"content": "ok"}}]}
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class ScriptedClient:
    """Cada instancia consume su propia cola: no hay fugas entre tests."""

    def __init__(self, responses, error, captured, client_kwargs):
        # Cola compartida entre intentos: los reintentos consumen la siguiente
        # respuesta, no reinician la secuencia.
        self.responses = responses if responses is not None else []
        self.error = error
        self.captured = captured
        self.client_kwargs = client_kwargs
        self.requests: list[dict] = []
        self.usages: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json, headers):
        self.requests.append({"url": url, "json": json, "headers": dict(headers)})
        if self.captured is not None:
            self.captured.append({"url": url, "json": json, "headers": dict(headers)})
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("No scripted response left: unexpected extra HTTP call")
        response = self.responses.pop(0)
        payload = getattr(response, "payload", None)
        if isinstance(payload, dict):
            self.usages.append(payload.get("usage") or {})
        return response


def run(coro):
    return asyncio.run(coro)


def content_response(text="ok", usage=None, finish_reason=None):
    choice = {"message": {"content": text}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    body = {"choices": [choice]}
    if usage is not None:
        body["usage"] = usage
    return MockResponse(payload=body)


def tool_response(name="getSiteFairs", arguments=None, usage=None):
    payload = {
        "choices": [{"message": {
            "content": None,
            "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }],
        }}],
    }
    if usage is not None:
        payload["usage"] = usage
    return MockResponse(payload=payload)


def make_provider(monkeypatch, *, response=None, responses=None, error=None,
                  captured=None, overrides=None):
    """Configura settings por inyección de módulo y devuelve (Provider, AgentError)."""
    values = {**DEFAULTS, **(overrides or {})}
    patched = dataclasses.replace(real_settings, **values)
    for name in SETTINGS_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "settings", patched, raising=False)

    queue = list(responses) if responses is not None else ([response] if response is not None else [])

    def client_factory(**kwargs):
        return ScriptedClient(queue, error, captured, kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    from app.errors import AgentError
    from app.providers.openai_compatible import OpenAICompatibleProvider
    return OpenAICompatibleProvider, AgentError


class EfficiencyMeter:
    """Medición de eficiencia exclusivamente en el harness de pruebas.

    No consulta observabilidad de producción ni instrumenta ``src``: todas las
    métricas se derivan de los payloads capturados por ``ScriptedClient`` o de
    valores aportados explícitamente por el escenario. Métricas implementadas:
      - tools anunciados por llamada (``tool_counts``);
      - tokens de entrada/salida/total informados por ``usage`` (``token_totals``);
      - llamadas LLM (un payload capturado = una petición HTTP, ``llm_calls``);
      - tamaño acumulado de resultados ``tool`` reenviados (``tool_result_bytes``);
      - latencias de LLM y de herramientas por separado, registradas únicamente
        con valores provistos por el escenario (``record_llm_latency`` y
        ``record_tool_latency``). No se mide ni se inventa latencia: si el
        escenario no aporta un valor, se reporta explícitamente como
        ``no disponible`` (``available=False`` en el resumen).
    """

    _UNAVAILABLE = "no disponible"

    def __init__(
        self,
        captured: list[dict],
        usages: list[dict] | None = None,
        llm_latencies: list[float] | None = None,
        tool_latencies: dict[str, list[float]] | None = None,
    ):
        self.captured = captured
        self.usages = usages or []
        self._llm_latencies: list[float] = []
        self._tool_latencies: dict[str, list[float]] = {}
        for seconds in llm_latencies or []:
            self.record_llm_latency(seconds)
        for name, values in (tool_latencies or {}).items():
            for seconds in values:
                self.record_tool_latency(name, seconds)

    def record_llm_latency(self, seconds: float) -> None:
        """Registra la latencia (s) de una llamada LLM aportada por el escenario."""
        self._llm_latencies.append(float(seconds))

    def record_tool_latency(self, tool_name: str, seconds: float) -> None:
        """Registra la latencia (s) de una herramienta aportada por el escenario."""
        self._tool_latencies.setdefault(tool_name, []).append(float(seconds))

    @classmethod
    def _summarize_latencies(cls, values: list[float]) -> dict:
        if not values:
            return {
                "available": False,
                "count": 0,
                "values_s": [],
                "total_s": cls._UNAVAILABLE,
                "mean_s": cls._UNAVAILABLE,
            }
        return {
            "available": True,
            "count": len(values),
            "values_s": list(values),
            "total_s": sum(values),
            "mean_s": sum(values) / len(values),
        }

    def llm_latency(self) -> dict:
        """Resumen de latencias LLM; ``no disponible`` si el escenario no las dio."""
        return self._summarize_latencies(self._llm_latencies)

    def tool_latency(self, tool_name: str | None = None) -> dict:
        """Resumen de latencias de herramientas, separadas de las del LLM.

        Con ``tool_name`` se limita a esa herramienta; sin él agrega todas y
        incluye un desglose ``by_tool``. Si no hay dato se marca ``no disponible``.
        """
        if tool_name is not None:
            return self._summarize_latencies(self._tool_latencies.get(tool_name, []))
        all_values = [value for values in self._tool_latencies.values() for value in values]
        summary = self._summarize_latencies(all_values)
        summary["by_tool"] = {
            name: self._summarize_latencies(values)
            for name, values in self._tool_latencies.items()
        }
        return summary

    def tool_counts(self) -> list[int]:
        return [len(request["json"].get("tools") or []) for request in self.captured]

    def llm_calls(self) -> int:
        return len(self.captured)

    def token_totals(self) -> dict:
        prompt = completion = total = 0
        for usage in self.usages:
            prompt += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            total += int(usage.get("total_tokens") or 0)
        return {
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": total,
        }

    def tool_result_bytes(self, messages: list[dict]) -> int:
        return sum(
            len((message.get("content") or "").encode("utf-8"))
            for message in messages
            if message.get("role") == "tool"
        )

    def snapshot(self, messages: list[dict] | None = None) -> dict:
        data = {
            "llm_calls": self.llm_calls(),
            "tools_per_call": self.tool_counts(),
            **self.token_totals(),
            "latency": {
                "llm": self.llm_latency(),
                "tools": self.tool_latency(),
            },
        }
        if messages is not None:
            data["tool_result_bytes"] = self.tool_result_bytes(messages)
        return data


class LogCapture:
    """Captura logs JSON de un logger concreto."""

    def __init__(self, name: str, level: int = logging.INFO):
        self.records: list[str] = []
        self.handler = logging.Handler()
        self.handler.emit = lambda record: self.records.append(self.handler.format(record))  # type: ignore[method-assign]
        self.logger = logging.getLogger(name)
        self.level = level

    def __enter__(self):
        self.previous = self.logger.level
        self.logger.setLevel(self.level)
        self.logger.addHandler(self.handler)
        return self

    def __exit__(self, *args):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.previous)

    def events(self, event: str) -> list[dict]:
        found = []
        for record in self.records:
            try:
                payload = json.loads(record)
            except (TypeError, ValueError):
                continue
            if payload.get("event") == event:
                found.append(payload)
        return found

    def text(self) -> str:
        return "\n".join(str(record) for record in self.records)
