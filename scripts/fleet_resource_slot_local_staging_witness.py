#!/usr/bin/env python3
"""Explicit, synthetic one-slot host-side LFM3 staging probe. Never admits Tasks.

Default: no effects. --live uses only the existing fixed LFM3 host loopback,
with a private disposable SQLite fixture, a fixed tiny prompt and no graph.
This is **not** a production allocation or owner-approval mechanism.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib import error, request

# Load only the reviewed ledger; the AssistX package normally initializes
# unrelated runtime services during import, which a staging witness must not.
LEDGER_PATH = Path(__file__).resolve().parents[1] / "src" / "assistx" / "fleet_resource_slot_ledger.py"
SPEC = importlib.util.spec_from_file_location("_host_fleet_slot_ledger_stage", LEDGER_PATH)
assert SPEC and SPEC.loader
SLOT_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SLOT_MODULE
SPEC.loader.exec_module(SLOT_MODULE)
FencedSlotLedger = SLOT_MODULE.FencedSlotLedger
SlotLeaseDenied = SLOT_MODULE.SlotLeaseDenied

ALIAS = "lfm3-1.7b-x1-fast"
UPSTREAM = "http://127.0.0.1:19181"
RUNTIME = "lfm3-x1-staging-only"
GENERATION = 897  # synthetic fixture, NOT production canonical approval
MARKER = "0" * 64  # synthetic fixture, NOT an owner-signed receipt
PROMPT = {
    "model": ALIAS,
    "messages": [{"role": "user", "content": "Reply OK."}],
    "max_tokens": 4,
    "temperature": 0,
    "stream": False,
}


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_json(opener, path, *, post=None):
    if path not in ("/health", "/v1/models", "/v1/chat/completions"):
        raise ValueError("path_not_allowlisted")
    req = request.Request(
        UPSTREAM + path,
        data=post,
        method="POST" if post else "GET",
        headers={"Content-Type": "application/json"} if post else {},
    )
    with opener.open(req, timeout=8) as resp:
        if resp.status != 200:
            raise ValueError("upstream_non200")
        raw = resp.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("oversized_upstream_reply")
    return json.loads(raw)


def provision_disposable_synthetic_db(db: Path, *, now_ms: int):
    """Test-only fixture. Do not call in an admitted gateway."""
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE resource_grant (
            id INTEGER PRIMARY KEY CHECK (id=1),runtime_id TEXT NOT NULL,
            generation INTEGER NOT NULL,capacity INTEGER NOT NULL,
            owner_receipt_sha256 TEXT NOT NULL,expires_at_ms INTEGER NOT NULL,
            status TEXT NOT NULL,clock_highwater_ms INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE resource_slot (
            slot INTEGER PRIMARY KEY,fence_epoch INTEGER NOT NULL,
            token_sha256 TEXT,task_id TEXT,claim_id TEXT,
            lease_expires_at_ms INTEGER NOT NULL)""")
        conn.execute(
            "INSERT INTO resource_grant VALUES (1,?,?,?,?,?,?,?)",
            (RUNTIME, GENERATION, 1, MARKER, now_ms + 20_000, "active", now_ms - 1),
        )
        conn.execute("INSERT INTO resource_slot VALUES (0,0,NULL,NULL,NULL,0)")
    db.chmod(0o600)


def probe():
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    health = _read_json(opener, "/health")
    catalog = _read_json(opener, "/v1/models")
    if (
        not isinstance(health, dict)
        or health.get("status") != "ok"
        or not isinstance(catalog, dict)
        or not isinstance(catalog.get("data"), list)
        or len(catalog["data"]) != 1
        or catalog["data"][0].get("id") != ALIAS
    ):
        raise ValueError("physical_model_not_verified")
    with tempfile.TemporaryDirectory(prefix="lfm3-slot-staging-") as staging:
        private = Path(staging)
        private.chmod(0o700)
        db = private / "synthetic-only.sqlite"
        now = int(time.time() * 1000)
        provision_disposable_synthetic_db(db, now_ms=now)
        ledger = FencedSlotLedger(
            db, runtime_id=RUNTIME, generation=GENERATION, capacity=1, owner_receipt_sha256=MARKER
        )
        first = ledger.acquire("staging-tiny-prompt", "synthetic-claim", duration_ms=12_000)
        exhausted = False
        try:
            ledger.acquire("staging-concurrent-deny", "synthetic-other", duration_ms=1000)
        except SlotLeaseDenied as exc:
            exhausted = str(exc) == "resource_slots_exhausted"
        if not exhausted:
            raise ValueError("overcommit_did_not_fail_closed")
        ledger.validate_effect(first)
        started = time.monotonic()
        model = _read_json(opener, "/v1/chat/completions", post=json.dumps(PROMPT, separators=(",", ":")).encode())
        latency_ms = round((time.monotonic() - started) * 1000)
        tokens = model.get("usage", {}).get("completion_tokens") if isinstance(model, dict) else None
        if type(tokens) is not int or not 1 <= tokens <= 4 or not model.get("choices"):
            raise ValueError("bounded_model_inference_invalid")
        ledger.validate_effect(first)
        ledger.release(first)
        stale_denied = False
        try:
            ledger.release(first)
        except SlotLeaseDenied:
            stale_denied = True
        if not stale_denied:
            raise ValueError("released_token_replay_not_denied")
        return {
            "physical_stage": "PASS",
            "upstream": "existing-LFM3-loopback-only",
            "completion_tokens": tokens,
            "elapsed_ms": latency_ms,
            "concurrent_second_slot_denied": True,
            "stale_release_token_denied": True,
            "db_deleted_after_probe": True,
            "synthetic_grant_only": True,
            "owner_signed": False,
            "production_resource_exclusivity_proven": False,
            "task_admission": "HOLD",
            "approved_parallel_slots": 0,
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true")
    opts = ap.parse_args(argv)
    if not opts.live:
        print(
            json.dumps(
                {
                    "status": "HOLD",
                    "reason": "EXPLICIT_STAGING_ONLY",
                    "task_admission": "HOLD",
                    "approved_parallel_slots": 0,
                }
            )
        )
        return 2
    try:
        result = probe()
        print(json.dumps(result, sort_keys=True))
    except (ValueError, RuntimeError, OSError, error.URLError, sqlite3.Error):
        print(
            json.dumps(
                {
                    "physical_stage": "HOLD",
                    "reason": "STAGING_PROOF_FAILED",
                    "task_admission": "HOLD",
                    "approved_parallel_slots": 0,
                }
            )
        )
    return 2  # never an authorizing process exit


if __name__ == "__main__":
    raise SystemExit(main())
