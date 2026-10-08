"""Read-only Neo4j claim proof gates. No graph calls or live tasks."""

from __future__ import annotations

import base64
import copy
import json
import os
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.trace_claim_lease import (
    is_trace_probe_candidate,
    is_trace_probe_task,
    issue_current_status,
    issue_lease_proof,
    load_signer,
    load_verifier,
    validate_claim,
    verify_lease_proof,
    verify_lease_signature,
)
from assistx.trace_claim_lease_api import build_claim_lease_router
from assistx.trace_execution_adapter import TraceDenied

NOW = 2_000_000_000_000


def task(now=NOW):
    return {
        "id": "task-101",
        "status": "CLAIMED",
        "kind": "trace_probe",
        "ticket_type": "trace_probe",
        "claimed_by": "xwing",
        "target_agent_id": "xwing",
        "claim_id": "claim-5",
        "execution_attempt": 2,
        "required_capabilities": ["trace-probe"],
        "lease_expires_at_ts": now + 60_000,
        "payload_json": json.dumps({"command_id": "probe.noop.v1"}),
    }


def issue(value, signer=None, now=NOW):
    return issue_lease_proof(
        value,
        task_id="task-101",
        claim_id="claim-5",
        node_id="xwing",
        execution_attempt=2,
        signer=signer or Ed25519PrivateKey.generate(),
        now_ms=now,
    )


def test_readonly_claim_snapshot_yields_short_node_bound_proof():
    signer = Ed25519PrivateKey.generate()
    proof = issue(task(), signer)
    assert proof["issuer"] == "assistx-neo4j-claimed-task"
    assert proof["node_id"] == "xwing"
    assert proof["execution_attempt"] == 2
    assert proof["expires_at_ms"] - proof["issued_at_ms"] == 10_000
    # A signed proof by itself MUST NOT be executable authority.
    with pytest.raises(TraceDenied, match="current_authority_not_proven"):
        verify_lease_proof(proof, node_id="xwing", verifier=signer.public_key(), now_ms=NOW)


@pytest.mark.parametrize(
    "update,error",
    [
        ({"status": "READY"}, "claim_inactive"),
        ({"status": "CANCELLED"}, "claim_inactive"),
        ({"status": "DONE"}, "claim_inactive"),
        ({"claimed_by": "scotts-macbook-air"}, "claim_wrong_node"),
        ({"target_agent_id": None}, "claim_wrong_node"),
        ({"claim_id": "replacement"}, "claim_superseded"),
        ({"execution_attempt": 3}, "claim_generation_mismatch"),
        ({"execution_attempt": None}, "claim_generation_mismatch"),
        ({"kind": "script"}, "claim_type_not_allowed"),
        ({"ticket_type": "task"}, "claim_type_not_allowed"),
        ({"task_type": "script"}, "claim_type_not_allowed"),
        ({"required_capabilities": ["script"]}, "claim_missing_capability"),
        ({"lease_expires_at_ts": NOW + 9_000}, "claim_lease_insufficient"),
        ({"lease_expires_at_ts": NOW - 1}, "claim_lease_insufficient"),
        ({"payload_json": '{"command_id":"shell.command"}'}, "claim_not_synthetic_noop"),
        ({"payload_json": '{"command_id":"probe.noop.v1","command":"id"}'}, "claim_not_synthetic_noop"),
        ({"payload_json": '{"malformed"'}, "invalid_claim_payload"),
    ],
)
def test_issuer_fails_closed_for_noncurrent_claims(update, error):
    original = task()
    original.update(update)
    with pytest.raises(TraceDenied, match=error):
        issue(original)


def test_issuer_requires_exact_node_task_and_generation():
    with pytest.raises(TraceDenied, match="claim_not_found"):
        issue(None)
    with pytest.raises(TraceDenied, match="claim_task_mismatch"):
        issue({**task(), "id": "a-different-task"})
    with pytest.raises(TraceDenied, match="invalid_execution_attempt"):
        issue_lease_proof(
            task(),
            task_id="task-101",
            claim_id="claim-5",
            node_id="xwing",
            execution_attempt=0,
            signer=Ed25519PrivateKey.generate(),
            now_ms=NOW,
        )


def test_signature_type_target_ttl_and_time_validation():
    signer = Ed25519PrivateKey.generate()
    proof = issue(task(), signer)
    cases = [
        ({"node_id": "scotts-macbook-air"}, "wrong_execution_node"),
        ({"claim_id": "new-claim"}, "lease_proof_signature_invalid"),
        ({"command_id": "shell.command"}, "lease_command_not_allowed"),
        ({"key_id": "elsewhere"}, "invalid_lease_proof_issuer"),
        ({"execution_attempt": 4}, "lease_proof_signature_invalid"),
        ({"expires_at_ms": NOW + 40_000}, "invalid_lease_proof_ttl"),
        ({"issuer": "offline-shadow-test-fixture"}, "invalid_lease_proof_issuer"),
        ({"extra": "authority"}, "invalid_lease_proof_schema"),
    ]
    for update, expected in cases:
        changed = {**proof, **update}
        with pytest.raises(TraceDenied, match=expected):
            verify_lease_signature(
                changed,
                node_id="xwing",
                verifier=signer.public_key(),
                now_ms=NOW + 1,
            )
    with pytest.raises(TraceDenied, match="lease_proof_expired_or_future"):
        verify_lease_signature(
            proof,
            node_id="xwing",
            verifier=signer.public_key(),
            now_ms=NOW + 10_001,
        )
    with pytest.raises(TraceDenied, match="lease_proof_signature_invalid"):
        verify_lease_signature(
            proof,
            node_id="xwing",
            verifier=Ed25519PrivateKey.generate().public_key(),
            now_ms=NOW + 1,
        )


def test_signing_key_must_be_owner_only_and_no_symlinks(tmp_path):
    signer = Ed25519PrivateKey.generate()
    path = tmp_path / "key.pem"
    path.write_bytes(
        signer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    assert isinstance(load_signer(path), Ed25519PrivateKey)
    path.chmod(0o644)
    with pytest.raises(TraceDenied, match="claim_key_unsafe"):
        load_signer(path)
    path.chmod(0o600)
    link = tmp_path / "key-symlink.pem"
    link.symlink_to(path)
    with pytest.raises(TraceDenied, match="claim_signing_key_unavailable"):
        load_signer(link)


def test_api_is_off_by_default_and_identity_checked_before_neo(monkeypatch, tmp_path):
    signer = Ed25519PrivateKey.generate()
    private = tmp_path / "private.pem"
    private.write_bytes(
        signer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)

    observed = {"reads": 0, "auth_checks": 0, "state": "CLAIMED"}

    class Neo:
        def get_task(self, task_id):
            observed["reads"] += 1
            assert task_id == "task-101"
            return {**task(now=int(time.time() * 1000)), "status": observed["state"]}

    def verify_node(node_id, token):
        observed["auth_checks"] += 1
        if node_id != "xwing" or token != "correct-node-token":
            raise HTTPException(status_code=403, detail="invalid node identity")

    app = FastAPI()
    app.include_router(
        build_claim_lease_router(
            neo_factory=lambda: Neo(),
            auth_dependency=lambda: "test-user",
            verify_node_identity=verify_node,
        )
    )
    client = TestClient(app)
    request = {
        "node_id": "xwing",
        "task_id": "task-101",
        "claim_id": "claim-5",
        "execution_attempt": 2,
    }
    monkeypatch.delenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", raising=False)
    monkeypatch.setenv("ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE", str(private))
    path = "/api/fleet/trace-execution/claim-lease-proof"
    response = client.post(path, json=request, headers={"x-fleet-node-token": "correct-node-token"})
    assert response.status_code == 503
    assert observed["reads"] == 0 and observed["auth_checks"] == 0
    monkeypatch.setenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", "true")
    assert client.post(path, json=request).status_code == 403
    assert observed["reads"] == 0
    response = client.post(path, json=request, headers={"x-fleet-node-token": "correct-node-token"})
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "verify-only-no-executor-activation"
    assert response.json()["lease_proof"]["issuer"] == "assistx-neo4j-claimed-task"
    assert observed["reads"] == 1
    # Caller cannot smuggle a shell command or changing task generation.
    assert (
        client.post(
            path, json={**request, "command_id": "shell.command"}, headers={"x-fleet-node-token": "correct-node-token"}
        ).status_code
        == 422
    )
    response = client.post(
        path, json={**request, "execution_attempt": 3}, headers={"x-fleet-node-token": "correct-node-token"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "claim_generation_mismatch"
    # A second API call must recheck the *current* graph task, not trust
    # an earlier signed lease after cancellation or re-claim.
    original = client.post(path, json=request, headers={"x-fleet-node-token": "correct-node-token"})
    assert original.status_code == 200
    lease = original.json()["lease_proof"]
    challenge = "d" * 64
    status_path = "/api/fleet/trace-execution/claim-current-status"
    payload = {"lease_proof": lease, "challenge": challenge}
    status_response = client.post(status_path, json=payload, headers={"x-fleet-node-token": "correct-node-token"})
    assert status_response.status_code == 200, status_response.text
    assert verify_lease_proof(
        lease,
        node_id="xwing",
        verifier=signer.public_key(),
        current_status=status_response.json()["current_status"],
        challenge=challenge,
    )
    observed["state"] = "CANCELLED"
    result = client.post(status_path, json=payload, headers={"x-fleet-node-token": "correct-node-token"})
    assert result.status_code == 409 and result.json()["detail"] == "claim_inactive"


def test_fresh_signed_status_requires_exact_challenge_and_current_claim():
    signer = Ed25519PrivateKey.generate()
    proof = issue(task(), signer)
    challenge = "a" * 64
    current = issue_current_status(
        task(now=NOW),
        lease_proof=proof,
        challenge=challenge,
        signer=signer,
        now_ms=NOW + 500,
    )
    assert verify_lease_proof(
        proof,
        node_id="xwing",
        verifier=signer.public_key(),
        current_status=current,
        challenge=challenge,
        now_ms=NOW + 600,
    )
    with pytest.raises(TraceDenied, match="current_status_challenge_mismatch"):
        verify_lease_proof(
            proof,
            node_id="xwing",
            verifier=signer.public_key(),
            current_status=current,
            challenge="b" * 64,
            now_ms=NOW + 600,
        )
    with pytest.raises(TraceDenied, match="current_status_stale_or_invalid"):
        verify_lease_proof(
            proof,
            node_id="xwing",
            verifier=signer.public_key(),
            current_status=current,
            challenge=challenge,
            now_ms=NOW + 2_001,
        )
    tampered = {**current, "expires_at_ms": current["expires_at_ms"] - 1}
    with pytest.raises(TraceDenied, match="current_status_signature_invalid"):
        verify_lease_proof(
            proof,
            node_id="xwing",
            verifier=signer.public_key(),
            current_status=tampered,
            challenge=challenge,
            now_ms=NOW + 600,
        )
    for change, expected in [
        ({"status": "CANCELLED"}, "claim_inactive"),
        ({"claim_id": "new-id"}, "claim_superseded"),
        ({"execution_attempt": 3}, "claim_generation_mismatch"),
    ]:
        changed = {**task(), **change}
        with pytest.raises(TraceDenied, match=expected):
            issue_current_status(
                changed,
                lease_proof=proof,
                challenge=challenge,
                signer=signer,
                now_ms=NOW + 600,
            )


def test_status_proof_cannot_be_minted_for_undisclosed_lease():
    signer = Ed25519PrivateKey.generate()
    proof = issue(task(), signer)
    challenge = "c" * 64
    forged = {**proof, "claim_id": "forged"}
    with pytest.raises(TraceDenied, match="lease_proof_signature_invalid"):
        issue_current_status(
            task(),
            lease_proof=forged,
            challenge=challenge,
            signer=signer,
            now_ms=NOW + 1,
        )
    with pytest.raises(TraceDenied, match="invalid_status_challenge"):
        issue_current_status(
            task(),
            lease_proof=proof,
            challenge="unsafe",
            signer=signer,
            now_ms=NOW + 1,
        )


@pytest.mark.parametrize(
    "broken",
    [
        {"kind": "trace_probe", "ticket_type": "task"},
        {"kind": "task", "ticket_type": "trace_probe"},
        {"kind": "trace_probe", "ticket_type": None},
        {"kind": None, "ticket_type": "trace_probe"},
        {"kind": "trace_probe", "ticket_type": "trace_probe", "task_type": "llm"},
    ],
)
def test_partial_trace_markers_use_protected_path_but_cannot_get_lease(broken):
    item = task()
    item.update(broken)
    assert is_trace_probe_candidate(item)
    assert not is_trace_probe_task(item)
    with pytest.raises(TraceDenied, match="claim_type_not_allowed"):
        issue(item)


def test_live_schema_matches_ticket_upsert_without_task_type():
    item = task()
    assert "task_type" not in item
    assert is_trace_probe_candidate(item) and is_trace_probe_task(item)
    signed = issue(item)
    assert signed["node_id"] == "xwing"
