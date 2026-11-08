"""Offline concurrent host-local resource-slot CAS and replay regressions.

All grants in this file are synthetic SQLite fixtures; no signer, source
Task, live graph, model endpoint or production slot is involved.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

# Avoid the full AssistX package import side effects in isolated slot tests.
LEDGER_PATH = Path(__file__).resolve().parents[1] / "src" / "assistx" / "fleet_resource_slot_ledger.py"
SPEC = importlib.util.spec_from_file_location("_host_fleet_slot_ledger_test", LEDGER_PATH)
assert SPEC and SPEC.loader
SLOT_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SLOT_MODULE
SPEC.loader.exec_module(SLOT_MODULE)
FencedSlotLedger = SLOT_MODULE.FencedSlotLedger
SlotLeaseDenied = SLOT_MODULE.SlotLeaseDenied

NOW = 1_794_104_000_000
DIGEST = "a" * 64
RUNTIME = "lfm3-x1-stage"
GEN = 897


def fixture_ledger(
    path: Path, *, capacity: int = 2, expires: int = NOW + 150_000, gen: int = GEN, status: str = "active"
) -> FencedSlotLedger:
    path.parent.mkdir(exist_ok=True)
    path.parent.chmod(0o700)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE resource_grant (
            id INTEGER PRIMARY KEY CHECK (id=1), runtime_id TEXT NOT NULL,
            generation INTEGER NOT NULL, capacity INTEGER NOT NULL,
            owner_receipt_sha256 TEXT NOT NULL, expires_at_ms INTEGER NOT NULL,
            status TEXT NOT NULL,clock_highwater_ms INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE resource_slot (
            slot INTEGER PRIMARY KEY, fence_epoch INTEGER NOT NULL,
            token_sha256 TEXT,task_id TEXT,claim_id TEXT,
            lease_expires_at_ms INTEGER NOT NULL)""")
        conn.execute(
            "INSERT INTO resource_grant VALUES (1,?,?,?,?,?,?,?)",
            (RUNTIME, gen, capacity, DIGEST, expires, status, NOW - 1),
        )
        conn.executemany("INSERT INTO resource_slot VALUES (?,0,NULL,NULL,NULL,0)", [(i,) for i in range(capacity)])
    path.chmod(0o600)
    return FencedSlotLedger(path, runtime_id=RUNTIME, generation=gen, capacity=capacity, owner_receipt_sha256=DIGEST)


@pytest.fixture
def ledger(tmp_path):
    return fixture_ledger(tmp_path / "sealed" / "slot.db")


def test_two_slots_atomic_with_owner_lease_and_nonplaintext_token(ledger):
    a = ledger.acquire("task-one", "claim-one", now_ms=NOW)
    b = ledger.acquire("task-two", "claim-two", now_ms=NOW)
    assert {a.slot, b.slot} == {0, 1}
    assert a.epoch == b.epoch == 1
    ledger.validate_effect(a, now_ms=NOW + 1000)
    ledger.validate_effect(b, now_ms=NOW + 1000)
    with pytest.raises(SlotLeaseDenied, match="resource_slots_exhausted"):
        ledger.acquire("task-three", "claim-three", now_ms=NOW + 1000)
    with sqlite3.connect(ledger.path) as c:
        stored = c.execute("SELECT token_sha256 FROM resource_slot").fetchall()
    assert all(a.token not in str(x) and b.token not in str(x) for x in stored)
    ledger.release(a, now_ms=NOW + 1500)
    c = ledger.acquire("task-three", "claim-three", now_ms=NOW + 1500)
    assert c.slot == a.slot and c.epoch == a.epoch + 1
    with pytest.raises(SlotLeaseDenied, match="fence_or_lease_invalid"):
        ledger.release(a, now_ms=NOW + 1500)
    ledger.validate_effect(c, now_ms=NOW + 1500)


def test_reuse_after_lease_expiry_advances_fence_and_rejects_original(ledger):
    first = ledger.acquire("task-one", "claim-one", duration_ms=500, now_ms=NOW)
    with pytest.raises(SlotLeaseDenied, match="fence_or_lease_invalid"):
        ledger.validate_effect(first, now_ms=NOW + 501)
    other = ledger.acquire("task-two", "claim-two", duration_ms=500, now_ms=NOW + 501)
    assert other.slot == first.slot and other.epoch == first.epoch + 1
    with pytest.raises(SlotLeaseDenied, match="fence_or_lease_invalid"):
        ledger.release(first, now_ms=NOW + 501)
    ledger.validate_effect(other, now_ms=NOW + 502)


def test_renew_invalidates_previous_lease_version_and_expiry(ledger):
    first = ledger.acquire("task-one", "claim-one", duration_ms=1000, now_ms=NOW)
    newer = ledger.renew(first, duration_ms=3000, now_ms=NOW + 500)
    assert newer.expires_at_ms == NOW + 3500
    assert first.epoch == newer.epoch
    with pytest.raises(SlotLeaseDenied, match="fence_or_lease_invalid"):
        ledger.validate_effect(first, now_ms=NOW + 550)
    with pytest.raises(SlotLeaseDenied, match="fence_or_lease_invalid"):
        ledger.release(first, now_ms=NOW + 550)
    ledger.validate_effect(newer, now_ms=NOW + 550)
    ledger.release(newer, now_ms=NOW + 550)


def test_duplicate_active_task_claim_never_gets_another_physical_slot(ledger):
    first = ledger.acquire("task-one", "claim-one", now_ms=NOW)
    with pytest.raises(SlotLeaseDenied, match="duplicate_active_task_claim"):
        ledger.acquire("task-one", "claim-one", now_ms=NOW)
    ledger.validate_effect(first, now_ms=NOW + 1)


@pytest.mark.parametrize(
    "change",
    [
        ("generation", 898),
        ("capacity", 3),
        ("owner_receipt_sha256", "f" * 64),
        ("status", "HOLD"),
        ("expires_at_ms", NOW),
    ],
)
def test_mutated_or_expired_grant_blocks_effect_and_new_claim(ledger, change):
    lease = ledger.acquire("task-one", "claim-one", now_ms=NOW)
    col, value = change
    with sqlite3.connect(ledger.path) as conn:
        conn.execute(f"UPDATE resource_grant SET {col}=? WHERE id=1", (value,))
    with pytest.raises(SlotLeaseDenied, match="resource_owner_grant_invalid_or_expired"):
        ledger.validate_effect(lease, now_ms=NOW + 1)
    with pytest.raises(SlotLeaseDenied, match="resource_owner_grant_invalid_or_expired"):
        ledger.acquire("task-two", "claim-two", now_ms=NOW + 1)
    with pytest.raises(SlotLeaseDenied, match="resource_owner_grant_invalid_or_expired"):
        ledger.release(lease, now_ms=NOW + 1)


@pytest.mark.parametrize(
    "invalid",
    [
        {"slot": 7},
        {"epoch": 4},
        {"runtime_id": "other-resource"},
        {"generation": 898},
        {"task_id": "other-task"},
        {"claim_id": "other-claim"},
        {"token": "a" * 64},
        {"expires_at_ms": NOW + 25_000},
    ],
)
def test_forged_or_replayed_lease_field_rejected(ledger, invalid):
    lease = ledger.acquire("task-one", "claim-one", now_ms=NOW)
    fake = replace(lease, **invalid)
    with pytest.raises(SlotLeaseDenied):
        ledger.validate_effect(fake, now_ms=NOW + 1)
    with pytest.raises(SlotLeaseDenied):
        ledger.release(fake, now_ms=NOW + 1)
    ledger.validate_effect(lease, now_ms=NOW + 1)


@pytest.mark.parametrize("duration", [0, -1, 30_001, True, 1.2])
def test_unbounded_or_noninteger_leases_refused(ledger, duration):
    with pytest.raises(SlotLeaseDenied, match="unbounded_resource_lease"):
        ledger.acquire("task-one", "claim-one", duration_ms=duration, now_ms=NOW)


def test_invalid_identity_clock_and_runtime_config_refused(ledger):
    for invalid in ("", "../../etc/passwd", "operator command", "bad\x00node"):
        with pytest.raises(SlotLeaseDenied, match="invalid_task_claim_identity"):
            ledger.acquire(invalid, "claim", now_ms=NOW)
    with pytest.raises(SlotLeaseDenied, match="resource_clock_invalid"):
        ledger.acquire("task", "claim", now_ms=True)
    with pytest.raises(SlotLeaseDenied, match="invalid_expected_resource_identity"):
        FencedSlotLedger(ledger.path, runtime_id="../wrong", generation=GEN, capacity=2, owner_receipt_sha256=DIGEST)


def test_missing_unprovisioned_db_or_foreign_location_never_self_creates(tmp_path):
    dir = tmp_path / "new"
    dir.mkdir(mode=0o700)
    target = dir / "missing.db"
    gateway = FencedSlotLedger(target, runtime_id=RUNTIME, generation=GEN, capacity=1, owner_receipt_sha256=DIGEST)
    with pytest.raises(SlotLeaseDenied):
        gateway.acquire("task", "claim", now_ms=NOW)
    assert not target.exists()


def test_world_readable_database_and_symlink_are_denied(ledger, tmp_path):
    ledger.path.chmod(0o644)
    with pytest.raises(SlotLeaseDenied, match="custody_invalid"):
        ledger.acquire("task", "claim", now_ms=NOW)
    ledger.path.chmod(0o600)
    sibling = ledger.path.with_name("symlink.db")
    sibling.symlink_to(ledger.path)
    gateway = FencedSlotLedger(sibling, runtime_id=RUNTIME, generation=GEN, capacity=2, owner_receipt_sha256=DIGEST)
    with pytest.raises(SlotLeaseDenied, match="custody_invalid"):
        gateway.acquire("task", "claim", now_ms=NOW)


def test_slot_count_tampering_denies_ledger(ledger):
    with sqlite3.connect(ledger.path) as c:
        c.execute("DELETE FROM resource_slot WHERE slot=1")
    with pytest.raises(SlotLeaseDenied, match="slot_layout_invalid"):
        ledger.acquire("task", "claim", now_ms=NOW)


def test_real_concurrent_processes_never_overcommit_capacity(ledger):
    # Lightweight real OS subprocesses: no pytest/numpy import in child.
    # Every process competes for the same *persistent* host-local SQLite DB.
    contender = Path(__file__).resolve().parent / "support" / "resource_slot_contender.py"
    project_src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(project_src), "PYTHONDONTWRITEBYTECODE": "1"}
    processes = [
        subprocess.Popen(
            [sys.executable, str(contender), str(ledger.path), f"process-{i}", str(NOW)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ledger.path.parent),
            env=env,
        )
        for i in range(12)
    ]
    results = []
    try:
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=12)
            assert proc.returncode in (0, 2), stderr[-300:]
            results.append(json.loads(stdout))
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)
    approved = [r for r in results if r["status"] == "PASS"]
    assert len(approved) == 2, results
    assert {r["slot"] for r in approved} == {0, 1}
    assert sum(r["status"] == "HOLD" for r in results) == 10


def test_threads_compete_using_atomic_sqlite_begin_immediate(ledger):
    def contestant(i):
        try:
            return ledger.acquire(f"thread-{i}", f"claim-{i}", now_ms=NOW)
        except SlotLeaseDenied:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        result = list(pool.map(contestant, range(30)))
    allowed = [x for x in result if x]
    assert len(allowed) == 2
    assert {x.slot for x in allowed} == {0, 1}


def test_same_task_new_claim_must_wait_for_prior_lease(ledger):
    active = ledger.acquire("same-task", "claim-1", now_ms=NOW)
    with pytest.raises(SlotLeaseDenied, match="duplicate_active_task_claim"):
        ledger.acquire("same-task", "claim-2", now_ms=NOW + 1)
    ledger.validate_effect(active, now_ms=NOW + 1)


def test_clock_rollback_fails_even_when_grant_and_slot_still_current(ledger):
    acquired = ledger.acquire("clock-task", "claim-a", now_ms=NOW + 20_000)
    with pytest.raises(SlotLeaseDenied, match="resource_clock_rollback_or_corruption"):
        ledger.validate_effect(acquired, now_ms=NOW)
    # The stored maximum time was preserved across the failed transaction.
    ledger.validate_effect(acquired, now_ms=NOW + 20_001)


def test_invalid_or_unrecognized_sqlite_schema_blocks_without_repair(tmp_path):
    private = tmp_path / "bad-db"
    private.mkdir(mode=0o700)
    target = private / "bad.sqlite"
    target.write_text("not actually a SQLite database")
    target.chmod(0o600)
    ledger = FencedSlotLedger(target, runtime_id=RUNTIME, generation=GEN, capacity=1, owner_receipt_sha256=DIGEST)
    with pytest.raises(SlotLeaseDenied, match="resource_ledger_transaction_failed"):
        ledger.acquire("task", "claim", now_ms=NOW)
    assert target.read_text() == "not actually a SQLite database"


def test_default_staging_cli_does_not_create_resource_grant_or_do_inference(tmp_path):
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "fleet_resource_slot_local_staging_witness.py"
    output = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert output.returncode == 2
    assert '"task_admission": "HOLD"' in output.stdout
    assert "EXPLICIT_STAGING_ONLY" in output.stdout
    assert list(tmp_path.iterdir()) == []
