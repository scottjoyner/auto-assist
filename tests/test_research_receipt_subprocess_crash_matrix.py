"""Real subprocess death vs disposable source and anchor SQLite COMMITs.

The child uses SIGKILL on ITS OWN PID, at fixed research checkpoints only.
No reboot, power loss, host/DBA separation, production state or release.
"""
from __future__ import annotations

import importlib.util
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests/fixtures/research_disposable_crash_child.py"
CONSUMER = ROOT / "scripts/research-consume-asymmetric-receipt.py"
SPEC = importlib.util.spec_from_file_location("crash_test_consumer", CONSUMER)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


@pytest.fixture
def disposable(tmp_path):
    resource = tmp_path / "research-resource.sqlite"
    anchor = tmp_path / "research-anchor.sqlite"
    (tmp_path / ".synthetic-crash-lab-only").write_text("DISPOSABLE_ONLY\n")
    lab.initialize_disposable_anchor(anchor)
    with sqlite3.connect(resource) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("""CREATE TABLE effect (
            resource_id TEXT PRIMARY KEY,term INTEGER,commit_seq INTEGER,
            generation INTEGER,checksum TEXT,revision_sha256 TEXT,
            committed INTEGER)""")
        db.execute("""CREATE TABLE receipt (
            term INTEGER,commit_seq INTEGER PRIMARY KEY,generation INTEGER,
            checksum TEXT,revision_sha256 TEXT,nonce TEXT)""")
    return resource, anchor


def read_table(path: Path, table: str) -> int:
    assert table in {"effect", "receipt", "spent_challenges", "consumed_receipts"}
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def child(resource: Path, anchor: Path, stage: str, *, opted_in=True):
    env = dict(os.environ)
    if opted_in:
        env["ALLOW_SYNTHETIC_CHILD_KILL"] = "TEST_FIXTURE_ONLY"
    else:
        env.pop("ALLOW_SYNTHETIC_CHILD_KILL", None)
    return subprocess.run(
        [sys.executable, str(CHILD), str(anchor), str(resource), stage],
        capture_output=True, text=True, env=env, timeout=12, check=False,
    )


@pytest.mark.parametrize(
    ("stage", "resource_rows", "anchor_sequence", "spent"),
    [
        ("source_pre_commit", 0, 11, 0),
        ("source_after_commit_before_anchor", 1, 11, 0),
        ("anchor_after_updates_before_commit", 1, 11, 0),
        ("anchor_after_commit_before_ack", 1, 12, 1),
    ],
)
def test_real_child_sigkill_at_transaction_boundaries(
    disposable, stage, resource_rows, anchor_sequence, spent,
):
    resource, anchor = disposable
    result = child(resource, anchor, stage)
    assert result.returncode == -signal.SIGKILL
    assert f"RESEARCH_STAGE={stage}" in result.stdout
    assert result.stderr == ""
    assert read_table(resource, "effect") == resource_rows
    assert read_table(resource, "receipt") == resource_rows
    assert read_table(anchor, "spent_challenges") == spent
    assert read_table(anchor, "consumed_receipts") == spent
    assert lab.read_anchor(anchor)["commit_seq"] == anchor_sequence
    assert resource.is_file() and anchor.is_file()


def test_no_crash_succeeds_and_all_disposable_rows_commit(disposable):
    resource, anchor = disposable
    result = child(resource, anchor, "no_crash")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY" in result.stdout
    assert read_table(resource, "effect") == 1
    assert read_table(resource, "receipt") == 1
    assert read_table(anchor, "spent_challenges") == 1
    assert read_table(anchor, "consumed_receipts") == 1
    assert lab.read_anchor(anchor)["commit_seq"] == 12


def test_postcommit_killed_child_has_no_ack_but_fresh_readback_sees_commit(disposable):
    resource, anchor = disposable
    result = child(resource, anchor, "anchor_after_commit_before_ack")
    assert result.returncode == -signal.SIGKILL
    assert lab.read_anchor(anchor)["commit_seq"] == 12
    with sqlite3.connect(f"{anchor.resolve().as_uri()}?mode=ro", uri=True) as observer:
        observer.execute("PRAGMA query_only=ON")
        assert observer.execute(
            "SELECT challenge,term,commit_seq FROM spent_challenges"
        ).fetchone() == ("a" * 64, 4, 12)
        assert observer.execute(
            "SELECT term,commit_seq FROM consumed_receipts"
        ).fetchone() == (4, 12)


def test_precommit_sigkill_rolls_back_all_anchor_ledger_updates(disposable):
    resource, anchor = disposable
    result = child(resource, anchor, "anchor_after_updates_before_commit")
    assert result.returncode == -signal.SIGKILL
    assert lab.read_anchor(anchor)["commit_seq"] == 11
    assert read_table(anchor, "spent_challenges") == 0
    assert read_table(anchor, "consumed_receipts") == 0


def test_default_child_does_not_execute_without_explicit_optin(disposable):
    resource, anchor = disposable
    result = child(resource, anchor, "no_crash", opted_in=False)
    assert result.returncode == 4
    assert read_table(resource, "effect") == 0
    assert lab.read_anchor(anchor)["commit_seq"] == 11


def test_child_refuses_absent_disposable_sentinel(disposable):
    resource, anchor = disposable
    (resource.parent / ".synthetic-crash-lab-only").unlink()
    result = child(resource, anchor, "no_crash")
    assert result.returncode == 7
    assert read_table(resource, "effect") == 0
    assert lab.read_anchor(anchor)["commit_seq"] == 11


def test_child_is_self_killing_only_with_no_production_entrypoint():
    child_code = CHILD.read_text()
    consumer_code = CONSUMER.read_text()
    assert "os.kill(os.getpid(), signal.SIGKILL)" in child_code
    assert "ALLOW_SYNTHETIC_CHILD_KILL" in child_code
    assert "NO_REAL_ANCHOR_OR_RELEASE_ENTRYPOINT" in consumer_code
    assert "_research_crash_hook" in consumer_code
