"""Actual OS process termination against independent disposable SQLite files.

The child SIGKILLs only itself at predetermined points. This is NOT power
loss, fsync failure, independently governed custody, or production authority.
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

spec = importlib.util.spec_from_file_location("crash_test_consumer", CONSUMER)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def lab(tmp_path):
    resource = tmp_path / "research-resource.sqlite"
    anchor = tmp_path / "research-anchor.sqlite"
    (tmp_path / ".synthetic-crash-lab-only").write_text("DISPOSABLE_ONLY\n")
    mod.initialize_disposable_anchor(anchor)
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


def inspect(path: Path, table: str) -> tuple[int, str]:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        count = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return count, "ok"


def run_worker(resource: Path, anchor: Path, stage: str):
    env = {**os.environ, "ALLOW_SYNTHETIC_CHILD_KILL": "TEST_FIXTURE_ONLY"}
    return subprocess.run(
        [sys.executable, str(CHILD), str(anchor), str(resource), stage],
        capture_output=True, text=True, env=env, timeout=12, check=False,
    )


@pytest.mark.parametrize(
    ("stage", "source_rows", "anchor_seq", "spent"),
    [
        ("source_pre_commit", 0, 11, 0),
        ("source_after_commit_before_anchor", 1, 11, 0),
        ("anchor_after_updates_before_commit", 1, 11, 0),
        ("anchor_after_commit_before_ack", 1, 12, 1),
    ],
)
@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_sigkill_child_at_real_transaction_boundaries(
    lab, stage, source_rows, anchor_seq, spent, journal_mode,
):
    resource, anchor = lab
    for path in (resource, anchor):
        with sqlite3.connect(path) as db:
            assert db.execute(
                f"PRAGMA journal_mode={journal_mode}"
            ).fetchone()[0].upper() == journal_mode
    result = run_worker(resource, anchor, stage)
    assert result.returncode == -signal.SIGKILL
    assert f"RESEARCH_STAGE={stage}" in result.stdout
    assert not result.stderr
    assert inspect(resource, "effect")[0] == source_rows
    assert inspect(resource, "receipt")[0] == source_rows
    assert inspect(anchor, "spent_challenges")[0] == spent
    assert inspect(anchor, "consumed_receipts")[0] == spent
    assert mod.read_anchor(anchor)["commit_seq"] == anchor_seq
    assert resource.is_file() and anchor.is_file()


def test_successful_child_without_crash_commits_both_anchor_ledgers(lab):
    resource, anchor = lab
    result = run_worker(resource, anchor, "no_crash")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY" in result.stdout
    assert inspect(resource, "effect")[0] == 1
    assert inspect(resource, "receipt")[0] == 1
    assert inspect(anchor, "spent_challenges")[0] == 1
    assert inspect(anchor, "consumed_receipts")[0] == 1
    assert mod.read_anchor(anchor)["commit_seq"] == 12


def test_crashed_postcommit_child_loses_ack_but_anchor_readback_is_durable(lab):
    resource, anchor = lab
    dead = run_worker(resource, anchor, "anchor_after_commit_before_ack")
    assert dead.returncode == -signal.SIGKILL
    # A fresh external observer sees a committed anchor despite no success
    # response. Its decision MUST NOT be to automatically reapply the effect.
    fresh_anchor = mod.read_anchor(anchor)
    assert fresh_anchor["commit_seq"] == 12
    with sqlite3.connect(f"{anchor.resolve().as_uri()}?mode=ro", uri=True) as observer:
        assert observer.execute(
            "SELECT challenge,term,commit_seq FROM spent_challenges"
        ).fetchone() == ("a" * 64, 4, 12)
        assert observer.execute(
            "SELECT term,commit_seq FROM consumed_receipts"
        ).fetchone() == (4, 12)


def test_crash_before_anchor_commit_never_lets_partial_ledger_escape(lab):
    resource, anchor = lab
    dead = run_worker(resource, anchor, "anchor_after_updates_before_commit")
    assert dead.returncode == -signal.SIGKILL
    assert mod.read_anchor(anchor)["commit_seq"] == 11
    with sqlite3.connect(f"{anchor.resolve().as_uri()}?mode=ro", uri=True) as observer:
        for table in ("spent_challenges", "consumed_receipts"):
            assert observer.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_child_guard_refuses_no_explicit_optin(lab):
    resource, anchor = lab
    env = {**os.environ}
    env.pop("ALLOW_SYNTHETIC_CHILD_KILL", None)
    result = subprocess.run(
        [sys.executable, str(CHILD), str(anchor), str(resource), "no_crash"],
        capture_output=True, text=True, env=env, timeout=8, check=False,
    )
    assert result.returncode == 4
    assert inspect(resource, "effect")[0] == 0
    assert mod.read_anchor(anchor)["commit_seq"] == 11


def test_child_guard_refuses_external_file_names_and_missing_marker(lab, tmp_path):
    resource, anchor = lab
    (tmp_path / ".synthetic-crash-lab-only").unlink()
    denied = run_worker(resource, anchor, "no_crash")
    assert denied.returncode == 7
    assert inspect(resource, "effect")[0] == 0
    assert mod.read_anchor(anchor)["commit_seq"] == 11


def test_crash_hook_is_research_only_no_release_entrypoint():
    assert "_research_crash_hook" in CONSUMER.read_text()
    source = CHILD.read_text()
    assert "os.kill(os.getpid(), signal.SIGKILL)" in source
    assert "ALLOW_SYNTHETIC_CHILD_KILL" in source
    assert "mode=ro" in source
    assert "NO_REAL_ANCHOR_OR_RELEASE_ENTRYPOINT" in CONSUMER.read_text()
