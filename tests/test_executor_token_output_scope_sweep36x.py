"""Sweep36x: executor token output allowance must not exceed the request scope.

Hermetic: synthetic Ed25519 keys and in-memory claim dicts only. No network,
no Neo4j, no router, no outbox, no service, no live configuration.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.executor_security import ExecutorTokenCodec
from assistx.task_family_routing import issue_executor_token

# Canonical ceiling applied by _install_executor_policy() when it builds the
# request payload that this token issuer scopes.
ROUTING_OUTPUT_CEILING = 32768
KEY_ID = "sweep36x-output-scope-key"


def _signer(monkeypatch) -> Ed25519PrivateKey:
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    monkeypatch.setenv("ASSISTX_EXECUTOR_SIGNING_KEY_PEM", pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", KEY_ID)
    return private


def _request(max_tokens: object, model: str = "auto/code") -> dict:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "bounded offline probe"}],
        "metadata": {
            "assistx_executor": {
                "task_id": "task-sweep36x-1",
                "claim_id": "claim-sweep36x-1",
                "agent_id": "fleet-executor",
                "projection_generation": 7,
            },
        },
    }


def _claims(private: Ed25519PrivateKey, request: dict) -> dict:
    token = issue_executor_token(request)
    return ExecutorTokenCodec(public_key=private.public_key(), key_id=KEY_ID).decode(token, audience="auto-router")


def test_request_cannot_inflate_token_output_scope(monkeypatch) -> None:
    private = _signer(monkeypatch)
    monkeypatch.delenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", raising=False)
    claims = _claims(private, _request(200_000))
    assert claims["max_output_tokens"] <= 8192


def test_configured_ceiling_is_clamped_to_routing_limit(monkeypatch) -> None:
    private = _signer(monkeypatch)
    monkeypatch.setenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", "65536")
    claims = _claims(private, _request(4096))
    assert claims["max_output_tokens"] <= ROUTING_OUTPUT_CEILING


def test_token_grants_only_requested_output_allowance(monkeypatch) -> None:
    private = _signer(monkeypatch)
    monkeypatch.delenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", raising=False)
    claims = _claims(private, _request(512))
    assert claims["max_output_tokens"] == 512


def test_zero_max_tokens_does_not_grant_full_environment_allowance(monkeypatch) -> None:
    private = _signer(monkeypatch)
    monkeypatch.delenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", raising=False)
    claims = _claims(private, _request(0))
    assert claims["max_output_tokens"] == 1


def test_negative_max_tokens_does_not_grant_full_environment_allowance(monkeypatch) -> None:
    private = _signer(monkeypatch)
    monkeypatch.delenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", raising=False)
    claims = _claims(private, _request(-5))
    assert claims["max_output_tokens"] == 1


def test_canonical_boundary_remains_available(monkeypatch) -> None:
    private = _signer(monkeypatch)
    monkeypatch.setenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", "32768")
    claims = _claims(private, _request(32768))
    assert claims["max_output_tokens"] == ROUTING_OUTPUT_CEILING


def test_token_remains_task_and_claim_scoped(monkeypatch) -> None:
    private = _signer(monkeypatch)
    claims = _claims(private, _request(512, model="auto/compress"))
    assert claims["task_id"] == "task-sweep36x-1"
    assert claims["claim_id"] == "claim-sweep36x-1"
    assert claims["allowed_model_aliases"][0] == "auto/compress"
