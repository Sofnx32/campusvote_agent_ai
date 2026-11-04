import os

os.environ["AI_SERVICE_TOKEN"] = "x" * 32

from app.context import verify_service_token
from app.errors import AgentError


def test_service_token_requires_exact_value():
    verify_service_token("Bearer " + "x" * 32)


def test_invalid_service_token_rejected():
    try:
        verify_service_token("Bearer wrong")
    except AgentError as error:
        assert error.code == "AI_SERVICE_UNAUTHORIZED"
    else:
        raise AssertionError("invalid service token was accepted")
