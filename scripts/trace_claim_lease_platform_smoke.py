#!/usr/bin/env python3
"""In-memory claim-proof portability test; NO AssistX IO or execution."""

from __future__ import annotations

import json
import secrets
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_claim_lease import issue_current_status, issue_lease_proof, verify_lease_proof
from assistx.trace_execution_adapter import TraceDenied


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", required=True)
    args = parser.parse_args()
    now = int(time.time() * 1000)
    # Fixture signer and claimed task exist only in this interpreter; NOT
    # an AssistX-issued claim or a production trust anchor.
    signer = Ed25519PrivateKey.generate()
    task = {
        "id": "offline-smoke-1",
        "status": "CLAIMED",
        "task_type": "trace_probe",
        "claimed_by": args.node_id,
        "target_agent_id": args.node_id,
        "claim_id": "offline-claim-1",
        "execution_attempt": 1,
        "required_capabilities": ["trace-probe"],
        "lease_expires_at_ts": now + 60_000,
        "payload_json": '{"command_id":"probe.noop.v1"}',
    }
    lease = issue_lease_proof(
        task,
        task_id=task["id"],
        claim_id=task["claim_id"],
        node_id=args.node_id,
        execution_attempt=1,
        signer=signer,
        now_ms=now,
    )
    challenge = secrets.token_hex(32)
    state = issue_current_status(
        task,
        lease_proof=lease,
        challenge=challenge,
        signer=signer,
        now_ms=now + 10,
    )
    digest = verify_lease_proof(
        lease,
        node_id=args.node_id,
        verifier=signer.public_key(),
        current_status=state,
        challenge=challenge,
        now_ms=now + 20,
    )
    rejected = []
    for case, kwargs in [
        ("missing_status", {"current_status": None, "challenge": challenge}),
        ("wrong_challenge", {"current_status": state, "challenge": secrets.token_hex(32)}),
    ]:
        try:
            verify_lease_proof(
                lease,
                node_id=args.node_id,
                verifier=signer.public_key(),
                now_ms=now + 20,
                **kwargs,
            )
        except TraceDenied:
            rejected.append(case)
    if rejected != ["missing_status", "wrong_challenge"]:
        raise SystemExit("FAIL: unsafe synthetic authorization")
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "in-memory-claim-fixture-only",
                "node_id": args.node_id,
                "lease_sha256": digest,
                "denied_cases": rejected,
                "executed_commands": 0,
                "assistx_claim_verified": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
