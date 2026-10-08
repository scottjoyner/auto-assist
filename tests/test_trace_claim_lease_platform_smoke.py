"""Fixture-only dual-node negative lease proof replay (no services required)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trace_claim_lease_platform_smoke.py"
EXPECTED = {
    "missing_status", "wrong_challenge", "wrong_node", "expired_status",
    "tampered_lease", "wrong_signer", "cancelled_before_status",
    "superseded_before_status", "generation_changed_before_status", "wrong_node_claim",
}


@pytest.mark.parametrize("node_id", ["xwing", "scotts-macbook-air"])
def test_in_memory_platform_smoke_has_ten_fail_closed_cases(node_id):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--node-id", node_id],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "ASSISTX_TRACE_LEASE_ISSUER_ENABLED": "false",
            "FLEET_TRACE_PROBE_ENABLED": "false",
            "FLEET_TRACE_REAL_EXECUTION_ENABLED": "false",
            "FLEET_UNSAFE_SHELL_TASKS_ENABLED": "false",
        },
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["mode"] == "in-memory-claim-fixture-only"
    assert report["node_id"] == node_id
    assert report["fixture_cases"] == 10
    assert set(report["denied_cases"]) == EXPECTED
    assert len(report["lease_sha256"]) == 64
    assert report["executed_commands"] == 0
    assert report["persisted_test_tasks"] == 0
    assert report["journal_mutations"] == 0
    for field in (
        "assistx_claim_verified", "production_issuer_verified",
        "physical_authenticated_negative_admission", "production_dispatch_authorized",
    ):
        assert report[field] is False


def test_unregistered_node_rejected_before_fixture():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--node-id", "unknown"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=20,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_direct_library_evaluation_never_accepts_arbitrary_node():
    spec = importlib.util.spec_from_file_location("trace_claim_lease_platform_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="node_not_in_shadow_allowlist"):
        module.evaluate("unknown-node")


def test_signed_status_does_not_prove_post_issue_revocation():
    """Characterize the 1.5s window; this is NOT instantaneous fencing.

    The in-memory node verifier has no live graph connection. If cancellation
    occurs AFTER the issuer signs status, a cryptographically valid proof is
    still accepted until expiry. This observation must keep privileged dispatch
    blocked instead of being misreported as revocation acceptance.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from assistx.trace_claim_lease import (
        issue_current_status,
        issue_lease_proof,
        verify_lease_proof,
    )
    from assistx.trace_execution_adapter import TraceDenied

    now = 2_000_000_000_000
    node = "xwing"
    task = {
        "id": "fixture-only", "status": "CLAIMED",
        "kind": "trace_probe", "ticket_type": "trace_probe",
        "claimed_by": node, "target_agent_id": node,
        "claim_id": "fixture-claim", "execution_attempt": 1,
        "required_capabilities": ["trace-probe"],
        "lease_expires_at_ts": now + 60_000,
        "payload_json": '{"command_id":"probe.noop.v1"}',
    }
    signer = Ed25519PrivateKey.generate()
    lease = issue_lease_proof(
        task, task_id=task["id"], claim_id=task["claim_id"],
        node_id=node, execution_attempt=1, signer=signer, now_ms=now,
    )
    challenge = "c" * 64
    status = issue_current_status(
        task, lease_proof=lease, challenge=challenge, signer=signer, now_ms=now + 10,
    )
    # Cancellation occurs in the synthetic authoritative snapshot only AFTER
    # the status is signed; verification has no independent revocation feed.
    task["status"] = "CANCELLED"
    digest = verify_lease_proof(
        lease, node_id=node, verifier=signer.public_key(),
        current_status=status, challenge=challenge, now_ms=now + 200,
    )
    assert isinstance(digest, str) and len(digest) == 64
    with pytest.raises(TraceDenied, match="current_status_stale_or_invalid"):
        verify_lease_proof(
            lease, node_id=node, verifier=signer.public_key(),
            current_status=status, challenge=challenge, now_ms=now + 1600,
        )
