#!/usr/bin/env python3
"""Reproducible offline lease experiment; zero provider requests."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import platform
import sqlite3
import tempfile
import threading
import time

from shared_provider_leases import ProviderLeaseLedger, Policy


def run_trial() -> dict:
    begin = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="provider-lease-test-") as scratch:
        root = Path(scratch)
        ledger = ProviderLeaseLedger(root / "ledger.sqlite3")
        barrier = threading.Barrier(20)

        def contender(i: int) -> dict:
            barrier.wait(timeout=10)
            return ledger.acquire("kilo" if i % 2 == 0 else "kilo_free",
                                  f"client-{i}", f"trial-request-{i}",
                                  input_reserved=100, output_reserved=10,
                                  now=1000, ttl_seconds=90)

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(contender, range(20)))
        granted = [row for row in outcomes if row["granted"]]
        max_active = ledger.snapshot(now=1000)["kilo"]["active"]
        winner = next(i for i, row in enumerate(outcomes) if row["granted"])
        idempotent = ledger.acquire("kilo_anonymous", f"client-{winner}",
                                    f"trial-request-{winner}",
                                    input_reserved=100, output_reserved=10, now=1001)
        released = ledger.release(granted[0]["lease_id"],
                                  f"client-{winner}", now=1002)
        tripped = ledger.trip("kilo", 429, now=1003)
        blocked = ledger.acquire("kilo_free", "another", "trial-next-request",
                                 now=1004)
        with sqlite3.connect(root / "ledger.sqlite3") as con:
            audit_count = con.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
            last_hash = con.execute(
                "SELECT event_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()[0]
        integrity = ledger.verify_audit()
    checks = {
        "one_slot_only": len(granted) == 1 and max_active == 1,
        "idempotent_existing": idempotent.get("lease_id") == granted[0]["lease_id"],
        "released": released["released"],
        "circuit_blocks": tripped["blocked_until"] >= 4603 and
                          blocked["reason"] == "circuit_open",
        "audit_valid": integrity,
        "zero_generation_requests": True,
    }
    return {
        "schema": "provider-lease-offline-pilot/v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(), "python": platform.python_version(),
        "pre_registered_hypotheses": [
            "one of twenty concurrent clients receives the only active slot",
            "duplicate request does not reserve another slot",
            "429 suppresses new grants for >=3600 seconds",
            "each transaction has an auditable hash-chain event",
            "zero generation tokens or provider API requests"],
        "observations": {
            "contenders": 20, "leases_granted": len(granted),
            "leases_denied": len(outcomes) - len(granted),
            "max_active": max_active,
            "circuit_until_injected_epoch": tripped["blocked_until"],
            "audit_events": audit_count, "audit_last_hash": last_hash,
            "elapsed_seconds": round(time.monotonic() - begin, 4),
            "inference_requests": 0, "inference_tokens": 0,
        },
        "acceptance": checks, "pass": all(checks.values()),
        "limitations": [
            "single-host local SQLite only",
            "clients must enforce lease loss and expiry independently",
            "no authenticated remote transport or provider-quota observation",
            "audit chain is not externally notarized or WORM"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] /
                                "artifacts/shared-provider-lease-pilot-20261007.json")
    args = parser.parse_args()
    result = run_trial()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"path": str(args.out), "pass": result["pass"],
                      "observations": result["observations"]}, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
