"""Static and synthetic contract for separate-host owner signing rehearsal.

Only the explicit CLI flag may reach Lenovo. Unit tests never perform SSH.
"""
from __future__ import annotations

import os
from pathlib import Path

import hermes_offhost_owner_signer_canary as canary


def test_default_inert_hold_and_no_production_mutation_by_default():
    import subprocess
    process=subprocess.run([
        "python3",str(Path(canary.__file__))],
        env={**os.environ,"PYTHONPATH":"src:tests"},
        text=True,capture_output=True,timeout=7,check=True)
    assert '"physical_executed": false' in process.stdout
    assert '"production_modified": false' in process.stdout


def test_exact_two_human_synthetic_claims_and_unique_nonces():
    body,claims=canary.payloads()
    assert [x["owner_subject"] for x in claims]==[
        "person-a","person-b","person-b"]
    assert all(x["schema"]==1 and x["aud"]==canary.AUDIENCE for x in claims)
    assert len({x["nonce"] for x in claims})==3
    assert all(x["method"]=="POST" and x["path"]=="/api/tasks" for x in claims)
    assert b"real-user" not in body
    assert "shared-two-owner-idempotency" in body.decode()


def test_offhost_signer_never_serializes_private_signing_key():
    source=canary.REMOTE
    assert "Ed25519PrivateKey.generate()" in source
    assert "public_bytes(" in source
    assert "signer.sign(raw)" in source
    assert "private_bytes(" not in source
    assert "print(json.dumps(" in source
    assert "private_key_exported" in source
    assert "open(" not in source


def test_real_ssh_host_key_is_pinned_with_noninteractive_batch():
    source=Path(canary.__file__).read_text()
    assert '"StrictHostKeyChecking=yes"' in source
    assert '"BatchMode=yes"' in source
    assert '"ConnectionAttempts=1"' in source
    assert '"lenovo",quoted' in source
    assert "password" in source  # synthetic Basic credentials in memory only
    for forbidden in ("sshpass", "StrictHostKeyChecking=no", "UserKnownHostsFile=/dev/null",
                      "docker stop", "docker restart", "docker exec"):
        assert forbidden not in source


def test_rehearsal_uses_real_checked_in_task_create_source_but_no_real_database():
    source=Path(canary.__file__).read_text()
    assert "exact_task_handler(" in source
    assert "Neo4jClient.__new__(Neo4jClient)" in source
    assert "MemoryStore()" in source
    assert "SignedGatewayOwnerMiddleware" in source
    assert '"production_task_mutations":0' in source
    assert '"real_provider_model_or_hermes_requests":0' in source
    assert '"independent_administrative_custody":False' in source


def test_cleans_local_signature_fixture_on_completion():
    source=Path(canary.__file__).read_text()
    assert 'with tempfile.TemporaryDirectory(prefix="assistx-owner-offhost-")' in source
    assert '"ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",old_key' in source
    assert '"ASSISTX_OWNER_GATEWAY_NONCE_DB",old_nonce' in source
    assert "must(shell(prod_cmd,timeout=10)==before" in source
