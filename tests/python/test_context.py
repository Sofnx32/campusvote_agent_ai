import os
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

os.environ["AI_SERVICE_TOKEN"] = "x" * 32

from app.config import settings
from app.context import verify_context, verify_service_token
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


def _signed_context(claims, private_key):
    return jwt.encode(claims, private_key, algorithm="RS256")


def _key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _claims(sites):
    now = datetime.now(timezone.utc)
    return {
        "iss": settings.context_issuer,
        "aud": settings.context_audience,
        "sub": "user-1",
        "role": "ADMIN",
        "scope_level": "SITE",
        "organization_id": "org-1",
        "site_ids": ["site-a", "site-b"],
        "sites": sites,
        "jti": "jti-1",
        "correlation_id": "corr-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
    }


def test_verify_context_requires_names_for_multiple_sites():
    private_pem, public_pem = _key_pair()
    previous = settings.context_public_key
    object.__setattr__(settings, "context_public_key", public_pem)
    try:
        token = _signed_context(
            _claims([{"id": "site-a", "name": "Sede Central"}]),
            private_pem,
        )
        try:
            verify_context(token)
        except AgentError as error:
            assert error.code == "AI_CONTEXT_INVALID"
        else:
            raise AssertionError("multi-site context without all names was accepted")
    finally:
        object.__setattr__(settings, "context_public_key", previous)


def test_verify_context_normalizes_authorized_site_names():
    private_pem, public_pem = _key_pair()
    previous = settings.context_public_key
    object.__setattr__(settings, "context_public_key", public_pem)
    try:
        token = _signed_context(
            _claims([
                {"id": "site-a", "name": "  Sede\nCentral  "},
                {"id": "site-b", "name": "Sede Norte"},
            ]),
            private_pem,
        )
        assert verify_context(token)["sites"] == [
            {"id": "site-a", "name": "Sede Central"},
            {"id": "site-b", "name": "Sede Norte"},
        ]
    finally:
        object.__setattr__(settings, "context_public_key", previous)
