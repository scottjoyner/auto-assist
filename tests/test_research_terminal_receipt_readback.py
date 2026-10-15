"""Read-only outcome inspection after real isolated child SIGKILL.

No fixture-authorized retry, promotion, merge, or production database access.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/research-terminal-receipt-readback.py"
CONSUMER = ROOT / "scripts/research-consume-asymmetric-receipt.py"
SPEC = importlib.util.spec_from_file_location("terminal_reconciliation_research", SOURCE)
assert SPEC and SPEC.loader
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)
SPEC2 = importlib.util.spec_from_file_location("terminal_consumer", CONSUMER)
assert SPEC2 and SPEC2.loader
consumer = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(consumer)
SPEC3 = importlib.util.spec_from_file_location(
    "crash_matrix_test", ROOT / "tests/test_research_receipt_subprocess_crash_matrix.py"
)
assert SPEC3 and SPEC3.loader
crash_matrix = importlib.util.module_from_spec(SPEC3)
SPEC3.loader.exec_module(crash_matrix)


def expected():
    claim = {
        "schema_version": 1,
        "audience": "synthetic-resource-commit-observation",
        "resource_id": "auto-router-runtime-projection",
        "term": 4, "commit_seq": 12, "generation": 27,
        "checksum": "b" * 64, "revision_sha256": "d" * 64,
        "nonce": "e" * 32, "challenge": "a" * 64,
        "key_epoch": 7,
        "issued_at_ms": 1792000000000 - 1000,
        "expires_at_ms": 1792000000000 + 5000,
    }
    return {
        "resource_id": claim["resource_id"],
        "term": 4, "previous_seq": 11, "commit_seq": 12,
        "generation": 27, "checksum": claim["checksum"],
        "revision_sha256": claim["revision_sha256"],
        "nonce": "e" * 32, "previous_nonce": "c" * 32,
        "challenge": "a" * 64,
        "receipt_digest": hashlib.sha256(
            consumer._verifier().canonical(claim)
        ).hexdigest(),
    }


@pytest.fixture
def pair(tmp_path):
    resource = tmp_path / "research-resource.sqlite"
    anchor = tmp_path / "research-anchor.sqlite"
    (tmp_path / ".synthetic-crash-lab-only").write_text("DISPOSABLE_ONLY\n")
    consumer.initialize_disposable_anchor(anchor)
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


def run_pair(pair, stage):
    resource, anchor = pair
    run = crash_matrix.child(resource, anchor, stage)
    assert (
        run.returncode == 0 if stage == "no_crash"
        else run.returncode == -crash_matrix.signal.SIGKILL
    ), (run.stdout, run.stderr)
    return research.assess(resource, anchor, expected=expected())


@pytest.mark.parametrize(("stage", "verdict"), [
    ("source_pre_commit", "SOURCE_NOT_OBSERVED_HOLD"),
    ("source_after_commit_before_anchor", "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"),
    ("anchor_after_updates_before_commit", "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"),
    ("anchor_after_commit_before_ack", "ANCHOR_CONSUMED_OBSERVED_HOLD"),
    ("no_crash", "ANCHOR_CONSUMED_OBSERVED_HOLD"),
])
@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_real_child_crash_reconciliation_remains_hold(pair, stage, verdict, journal):
    resource, anchor = pair
    for path in (resource, anchor):
        with sqlite3.connect(path) as db:
            assert db.execute(
                f"PRAGMA journal_mode={journal}"
            ).fetchone()[0].upper() == journal
    report = run_pair(pair, stage)
    assert report == {
        "schema_version": 1, "release_authorized": False,
        "retry_authorized": False, "verdict": verdict,
    }


def test_missing_resource_yields_unavailable_no_go(pair):
    resource, anchor = pair
    resource.unlink()
    result = research.assess(resource, anchor, expected=expected())
    assert result["verdict"] == "DISPOSABLE_SCOPE_INVALID_NO_GO"
    assert result["retry_authorized"] is False
    assert not resource.exists()


def test_missing_anchor_is_never_created(pair):
    resource, anchor = pair
    anchor.unlink()
    assert research.assess(resource, anchor, expected=expected())["verdict"] == (
        "DISPOSABLE_SCOPE_INVALID_NO_GO"
    )
    assert not anchor.exists()


@pytest.mark.parametrize("bad", [
    {}, None, "not a dictionary", {"resource_id": "fake"},
    dict(expected(), commit_seq=13),
    dict(expected(), previous_seq=True),
    dict(expected(), receipt_digest="0" * 63),
])
def test_missing_or_untrusted_external_expected_evidence_denies(pair, bad):
    result = research.assess(*pair, expected=bad)
    assert result["verdict"] == "EXPECTED_EVIDENCE_INVALID_NO_GO"
    assert not result["release_authorized"] and not result["retry_authorized"]


def test_source_receipt_mismatch_never_approves(pair):
    source, anchor = pair
    run_pair(pair, "source_after_commit_before_anchor")
    with sqlite3.connect(source) as db:
        db.execute("UPDATE receipt SET generation=92")
    result = research.assess(source, anchor, expected=expected())
    assert result["verdict"] == "RESOURCE_MISMATCH_NO_GO"


def test_anchor_highwater_without_consumed_ledger_denies(pair):
    source, anchor = pair
    run_pair(pair, "source_after_commit_before_anchor")
    with sqlite3.connect(anchor) as db:
        db.execute(
            "UPDATE highwater SET commit_seq=12,last_nonce=?",
            ("e" * 32,),
        )
    assert research.assess(source, anchor, expected=expected())["verdict"] == (
        "ANCHOR_OR_LEDGER_CONTRADICTION_NO_GO"
    )


def test_tampered_ledger_challenge_denies(pair):
    source, anchor = pair
    run_pair(pair, "no_crash")
    with sqlite3.connect(anchor) as db:
        db.execute("UPDATE spent_challenges SET challenge=?", ("0" * 64,))
    assert research.assess(source, anchor, expected=expected())["verdict"] == (
        "ANCHOR_OR_LEDGER_CONTRADICTION_NO_GO"
    )


def test_tampered_but_consistent_digest_denies_independent_expectation(pair):
    source, anchor = pair
    run_pair(pair, "no_crash")
    with sqlite3.connect(anchor) as db:
        db.execute("UPDATE spent_challenges SET receipt_digest=?", ("0" * 64,))
        db.execute("UPDATE consumed_receipts SET receipt_digest=?", ("0" * 64,))
    assert research.assess(source, anchor, expected=expected())["verdict"] == (
        "ANCHOR_OR_LEDGER_CONTRADICTION_NO_GO"
    )


def test_ahead_anchor_with_fictional_sequence_denies(pair):
    source, anchor = pair
    run_pair(pair, "no_crash")
    with sqlite3.connect(anchor) as db:
        db.execute("UPDATE highwater SET commit_seq=13")
    assert research.assess(source, anchor, expected=expected())["verdict"] == (
        "ANCHOR_OR_LEDGER_CONTRADICTION_NO_GO"
    )


def test_full_anchor_restore_indistinguishable_from_pending_no_go(pair, tmp_path):
    source, anchor = pair
    snapshot = tmp_path / "copy-before.sqlite"
    shutil.copy2(anchor, snapshot)
    assert run_pair(pair, "no_crash")["verdict"] == "ANCHOR_CONSUMED_OBSERVED_HOLD"
    shutil.copy2(snapshot, anchor)
    report = research.assess(source, anchor, expected=expected())
    assert report["verdict"] == "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"
    # Prior consumption is undetectable without genuinely external highwater.
    assert report["retry_authorized"] is False


def test_changed_between_snapshots_fails_closed(pair, monkeypatch):
    source, anchor = pair
    run_pair(pair, "source_after_commit_before_anchor")
    original = research._read_snapshot
    call_count = 0

    def mutate_after_first(*args):
        nonlocal call_count
        snapshot = original(*args)
        call_count += 1
        if call_count == 1:
            with sqlite3.connect(anchor) as db:
                db.execute("UPDATE highwater SET commit_seq=13")
        return snapshot

    monkeypatch.setattr(research, "_read_snapshot", mutate_after_first)
    report = research.assess(source, anchor, expected=expected())
    assert report["verdict"] == "CHANGED_BETWEEN_READBACKS_NO_GO"


def test_unreadable_schema_causes_fail_closed_unknown(pair):
    source, anchor = pair
    with sqlite3.connect(anchor) as db:
        db.execute("DROP TABLE consumed_receipts")
    report = research.assess(source, anchor, expected=expected())
    assert report["verdict"] == "UNAVAILABLE_OR_UNTRUSTED_READBACK_NO_GO"


def test_symlinked_source_denied_without_opening(pair, tmp_path):
    source, anchor = pair
    source.rename(tmp_path / "moved-source.sqlite")
    source.symlink_to(tmp_path / "moved-source.sqlite")
    assert research.assess(source, anchor, expected=expected())["verdict"] == (
        "DISPOSABLE_SCOPE_INVALID_NO_GO"
    )


def test_no_mutation_or_operational_entrypoint():
    code = SOURCE.read_text()
    assert 'NO_TERMINAL_AUTHORITY_OR_RETRY_ENTRYPOINT' in code
    assert "mode=ro" in code and "PRAGMA query_only=ON" in code
    assert "UPDATE highwater SET" not in code
    assert "INSERT INTO spent_challenges" not in code
    assert "RESEARCH_ONLY" in code
