"""Regresión: argumentos con markdown/prosa y errores con detalles forenses."""

from __future__ import annotations

import json

import pytest

from provider_helpers import LogCapture, MockResponse, make_provider, run

CONTEXT = {"site_id": "site-a"}
MESSAGES = [{"role": "user", "content": "crea categoría con rúbrica"}]


def tool_response_with_arguments(arguments):
    return MockResponse(payload={
        "choices": [{"message": {
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "setup_category_with_rubric", "arguments": arguments},
            }],
        }}],
    })


def _result(monkeypatch, arguments):
    Provider, _ = make_provider(monkeypatch, response=tool_response_with_arguments(arguments))
    return run(Provider().complete(CONTEXT, MESSAGES))


def test_fenced_json_arguments_are_parsed(monkeypatch):
    payload = {"fair_id": "f1", "category": {"name": "Ciencias"}}
    result = _result(monkeypatch, "```json\n" + json.dumps(payload) + "\n```")
    assert result.tool_calls[0].arguments == payload


def test_prose_around_json_arguments_is_stripped(monkeypatch):
    payload = {"fair_id": "f1", "points": 20}
    raw = "Aquí tienes los datos: " + json.dumps(payload) + " Espero que sirva."
    result = _result(monkeypatch, raw)
    assert result.tool_calls[0].arguments == payload


def test_nested_fenced_json_string_is_coerced(monkeypatch):
    criteria = [{"name": "Metodología", "points": 8}]
    payload = {
        "fair_id": "f1",
        "criteria": "```json\n" + json.dumps(criteria) + "\n```",
    }
    result = _result(monkeypatch, json.dumps(payload))
    assert result.tool_calls[0].arguments["criteria"] == criteria


def test_invalid_json_details_include_exact_reason(monkeypatch):
    raw = '```json\n{"fair_id": "f1"\n```'
    with LogCapture("campusvote.forensic") as logs:
        Provider, AgentError = make_provider(
            monkeypatch, response=tool_response_with_arguments(raw),
        )
        with pytest.raises(AgentError) as error:
            run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "AI_RESPONSE_INVALID"
    details = error.value.details
    assert details["reason"] == "INVALID_TOOL_ARGUMENTS_JSON"
    assert details["failure"] == "json_decode_error"
    assert details["tool"] == "setup_category_with_rubric"
    assert details["json_error"]
    events = logs.events("llm_invalid_response")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_tool_json"
    assert events[0]["failure"] == details["failure"]
    assert events[0]["excerpt"]


@pytest.mark.parametrize("raw", ["[]", "42", '"texto"', "false"])
def test_non_object_json_details_are_not_null(monkeypatch, raw):
    Provider, AgentError = make_provider(
        monkeypatch, response=tool_response_with_arguments(raw),
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.details["reason"] == "INVALID_TOOL_ARGUMENTS_JSON"
    assert error.value.details["failure"].startswith("not_object")


def test_empty_content_details_include_finish_reason(monkeypatch):
    body = {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]}
    Provider, AgentError = make_provider(
        monkeypatch, response=MockResponse(payload=body),
    )
    with pytest.raises(AgentError) as error:
        run(Provider().complete(CONTEXT, MESSAGES))
    assert error.value.code == "AI_RESPONSE_INVALID"
    assert error.value.details == {"reason": "EMPTY_CONTENT", "finish_reason": "stop"}
