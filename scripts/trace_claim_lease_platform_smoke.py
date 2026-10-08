#!/usr/bin/env python3
"""In-memory claim-proof negative tests: no AssistX IO or execution.

This fixture is NOT authenticated physical admission or production authority.
Use PYTHONDONTWRITEBYTECODE=1 to avoid interpreter cache files.
"""

from __future__ import annotations

import argparse
import json
import secrets
import time
from collections.abc import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_claim_lease import issue_current_status, issue_lease_proof, verify_lease_proof
from assistx.trace_execution_adapter import TraceDenied

NODES = ("xwing", "scotts-macbook-air")
DENIALS = (
    "missing_status", "wrong_challenge", "wrong_node", "expired_status",
    "tampered_lease", "wrong_signer", "cancelled_before_status",
    "superseded_before_status", "generation_changed_before_status", "wrong_node_claim",
)


def _deny(name: str, check: Callable[[], object], rejected: list[str]) -> None:
    try:
        check()
    except TraceDenied:
        rejected.append(name)
    else:
        raise RuntimeError("unsafe_fixture_admitted_" + name)


def evaluate(node_id: str, *, now_ms: int | None = None) -> dict[str, object]:
    if node_id not in NODES:
        raise ValueError("node_not_in_shadow_allowlist")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    signer = Ed25519PrivateKey.generate()
    task = {
        "id": "offline-smoke-1", "status": "CLAIMED",
        # Canonical current graph schema, not the deprecated task_type shortcut.
        "kind": "trace_probe", "ticket_type": "trace_probe",
        "claimed_by": node_id, "target_agent_id": node_id,
        "claim_id": "offline-claim-1", "execution_attempt": 1,
        "required_capabilities": ["trace-probe"],
        "lease_expires_at_ts": now + 60000,
        "payload_json": '{"command_id":"probe.noop.v1"}',
    }
    lease = issue_lease_proof(
        task, task_id=task["id"], claim_id=task["claim_id"],
        node_id=node_id, execution_attempt=1, signer=signer, now_ms=now,
    )
    challenge = secrets.token_hex(32)
    status = issue_current_status(
        task, lease_proof=lease, challenge=challenge, signer=signer, now_ms=now + 10,
    )

    def verify(**overrides: object) -> str:
        params = {
            "node_id": node_id, "verifier": signer.public_key(),
            "current_status": status, "challenge": challenge, "now_ms": now + 20,
        }
        params.update(overrides)
        return verify_lease_proof(lease, **params)

    digest = verify()
    denied: list[str] = []
    _deny("missing_status", lambda: verify(current_status=None), denied)
    _deny("wrong_challenge", lambda: verify(challenge=secrets.token_hex(32)), denied)
    other = next(value for value in NODES if value != node_id)
    _deny("wrong_node", lambda: verify(node_id=other), denied)
    _deny("expired_status", lambda: verify(now_ms=now + 2000), denied)
    _deny(
        "tampered_lease",
        lambda: verify_lease_proof(
            {**lease, "claim_id": "forged"}, node_id=node_id,
            verifier=signer.public_key(), current_status=status,
            challenge=challenge, now_ms=now + 20,
        ),
        denied,
    )
    _deny("wrong_signer", lambda: verify(verifier=Ed25519PrivateKey.generate().public_key()), denied)

    def mint_from(change: dict[str, object]) -> object:
        return issue_current_status(
            {**task, **change}, lease_proof=lease, challenge=secrets.token_hex(32),
            signer=signer, now_ms=now + 20,
        )

    _deny("cancelled_before_status", lambda: mint_from({"status": "CANCELLED"}), denied)
    _deny("superseded_before_status", lambda: mint_from({"claim_id": "new-claim"}), denied)
    _deny("generation_changed_before_status", lambda: mint_from({"execution_attempt": 2}), denied)
    _deny("wrong_node_claim", lambda: mint_from({"claimed_by": other}), denied)
    if tuple(denied) != DENIALS:
        raise RuntimeError("fixture_denials_incomplete")
    return {
        "ok": True,
        "mode": "in-memory-claim-fixture-only",
        "node_id": node_id,
        "lease_sha256": digest,
        "denied_cases": denied,
        "fixture_cases": len(denied),
        "executed_commands": 0,
        "persisted_test_tasks": 0,
        "journal_mutations": 0,
        "assistx_claim_verified": False,
        "production_issuer_verified": False,
        "physical_authenticated_negative_admission": False,
        "production_dispatch_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", required=True, choices=NODES)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.node_id), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
