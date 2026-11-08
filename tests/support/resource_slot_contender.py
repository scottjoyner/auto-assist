"""Minimal non-pytest subprocess for a synthetic cross-process lease race.

Never creates a grant or accesses a model. No token is printed to stdout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

LEDGER_PATH = Path(__file__).resolve().parents[2] / "src" / "assistx" / "fleet_resource_slot_ledger.py"
SPEC = importlib.util.spec_from_file_location("_host_fleet_slot_ledger_subprocess", LEDGER_PATH)
assert SPEC and SPEC.loader
SLOT_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SLOT_MODULE
SPEC.loader.exec_module(SLOT_MODULE)
FencedSlotLedger = SLOT_MODULE.FencedSlotLedger
SlotLeaseDenied = SLOT_MODULE.SlotLeaseDenied


def main():
    if len(sys.argv) != 4:
        return 2
    db, task, now_ms = sys.argv[1:]
    try:
        ledger = FencedSlotLedger(
            db,
            runtime_id="lfm3-x1-stage",
            generation=897,
            capacity=2,
            owner_receipt_sha256="a" * 64,
        )
        lease = ledger.acquire(task, "claim-" + task, now_ms=int(now_ms))
        print(json.dumps({"status": "PASS", "slot": lease.slot, "epoch": lease.epoch}))
        return 0
    except (SlotLeaseDenied, ValueError):
        print(json.dumps({"status": "HOLD"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
