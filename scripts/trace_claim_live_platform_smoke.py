#!/usr/bin/env python3
"""Fixture-proof NOOP on a physical shadow node, with fsynced audit proof.

No AssistX API, persistent real issuer, remote network, or shell execution.
Only append two rows to the node's EXISTING trace-shadow journal.
"""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_claim_lease import issue_current_status, issue_lease_proof
from assistx.trace_claim_live_executor import execute_authorized_probe
from assistx.trace_execution_adapter import TraceReceiptStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", choices=["xwing", "scotts-macbook-air"], required=True)
    parser.add_argument("--audit-root", required=True)
    args = parser.parse_args()
    signer = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    task_id = "shadow-live-proof-" + secrets.token_hex(12)
    claim_id = "fixture-claim-" + secrets.token_hex(12)
    task = {
        "id": task_id,
        "kind": "trace_probe",
        "ticket_type": "trace_probe",
        "status": "CLAIMED",
        "target_agent_id": args.node_id,
        "claimed_by": args.node_id,
        "claim_id": claim_id,
        "execution_attempt": 1,
        "lease_expires_at_ts": now + 60000,
        "required_capabilities": ["trace-probe"],
        "payload_json": '{"command_id":"probe.noop.v1"}',
    }
    store = TraceReceiptStore(args.audit_root, node_id=args.node_id)
    before = store.verify()
    with tempfile.TemporaryDirectory(prefix="shadow-fixture-proof-") as dirname:
        public = Path(dirname) / "pinned-public.pem"
        public.write_bytes(
            signer.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        public.chmod(0o600)
        env = {
            "FLEET_TRACE_PROBE_ENABLED": "true",
            "FLEET_TRACE_REAL_EXECUTION_ENABLED": "true",
            "FLEET_NODE_AUTH_TOKEN": "offline-test-fixture-token",
            "FLEET_TRACE_LEASE_VERIFIER_KEY_FILE": str(public),
        }
        calls = []

        def http(method, url, *, data=None, **kwargs):
            calls.append(url)
            if url.endswith("/claim-lease-proof"):
                proof = issue_lease_proof(
                    task,
                    task_id=task_id,
                    claim_id=claim_id,
                    node_id=args.node_id,
                    execution_attempt=1,
                    signer=signer,
                )
                return 200, {"lease_proof": proof}
            if url.endswith("/claim-current-status"):
                current = issue_current_status(
                    task,
                    lease_proof=data["lease_proof"],
                    challenge=data["challenge"],
                    signer=signer,
                )
                return 200, {"current_status": current}
            raise AssertionError("unrecognized_fixture_url")

        result = execute_authorized_probe(
            task=task,
            node_id=args.node_id,
            claim_id=claim_id,
            audit_root=args.audit_root,
            assistx_url="https://fixture.invalid",
            auth=None,
            env=env,
            http=http,
        )
    after = store.verify()
    passed = (
        result["status"] == "DONE"
        and result["assistx_claim_verified"]
        and after["records"] == before["records"] + 2
        and len(calls) == 2
    )
    print(
        json.dumps(
            {
                "node_id": args.node_id,
                "ok": passed,
                "mode": "offline-fixture-proofs",
                "assistx_claim_issuer_verified": False,
                "executed_os_commands": 0,
                "before_records": before["records"],
                "after_records": after["records"],
                "signed_noop_receipt": result["trace"]["completed_hash"],
                "final_audit_head": after["last_hash"],
                "transport": "in-process",
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
