"""Disposable effect-time term/owner/epoch gating against stale receipts.

Two unrelated SQLite databases and one test-process-controlled key space.
This does not demonstrate production dispatch or independent OS/DBA custody.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/research-effect-time-term-fence.py"
SPEC = importlib.util.spec_from_file_location("effect_time_test_module", SCRIPT)
assert SPEC and SPEC.loader
fence = importlib.util.module_from_spec(SPEC)
SPEC2 = importlib.util.spec_from_file_location(
    "prior_outbox_recovery", ROOT / "tests/test_research_verified_outbox_recovery.py"
)
assert SPEC2 and SPEC2.loader
prior = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(prior)
SPEC.loader.exec_module(fence)


@pytest.fixture
def lab(tmp_path):
    state = prior.lab.__wrapped__(tmp_path)
    assert state["attempt"]()[0] is True
    path = tmp_path / "synthetic-fenced-effects.sqlite"
    fence.bootstrap(path)
    def run(*, after=None, while_locked=None, ack=None, epoch=9, owner="owner-a",
            bundle_hash=None, effect_root=None):
        return fence.execute_once(
            path, state["outbox_member"], state["resource"],
            state["anchor"], state["artifact"], expected=state["expected"],
            bundle_sha256=state["pin"] if bundle_hash is None else bundle_hash,
            previous_head_hash=state["baseline"]["head_hash"],
            effect_root=state["effect_root"] if effect_root is None else effect_root,
            witness_root=state["witness_root"],
            challenge=prior.previous.CHALLENGE,
            now_ms=prior.previous.NOW, required_epoch=epoch, required_owner=owner,
            after_verify=after, during_effect_lock=while_locked,
            after_commit=ack,
        )
    def count():
        result = fence.inspect_effect(path)
        assert result["release_authorized"] is False
        assert result["retry_authorized"] is False
        return result["count"]
    return {**state,"journal":path,"run":run,"count":count}


def test_verified_effect_time_applies_once_and_no_replay(lab):
    assert lab["run"]() == (True,"RESEARCH_LOCAL_EFFECT_TIME_FENCE_ONLY_HOLD")
    assert lab["count"]() == 1
    assert lab["run"]() == (False,"EFFECT_ALREADY_APPLIED_NO_RETRY")
    assert lab["count"]() == 1


def test_owner_term_revoked_between_verified_readback_and_effect_denies(lab):
    def revoke():
        assert fence.rotate(
            lab["journal"],term=5,epoch=10,owner="owner-b",revoked=True
        )
    assert lab["run"](after=revoke) == (
        False,"STALE_OR_REVOKED_OWNER_AT_EFFECT_TIME_NO_GO"
    )
    assert lab["count"]() == 0


def test_owner_epoch_revoked_same_term_between_verify_and_effect_denies(lab):
    def revoke():
        assert fence.rotate(
            lab["journal"],term=4,epoch=10,owner="owner-b",revoked=True
        )
    assert lab["run"](after=revoke) == (
        False,"STALE_OR_REVOKED_OWNER_AT_EFFECT_TIME_NO_GO"
    )
    assert lab["count"]() == 0


def test_after_verify_source_effect_mutation_rechecked_at_effect_time(lab):
    def mutate():
        with sqlite3.connect(lab["resource"]) as admin:
            admin.execute("UPDATE effect SET generation=1001")
    assert lab["run"](after=mutate) == (
        False,"EVIDENCE_CHANGED_AT_EFFECT_TIME_NO_GO"
    )
    assert lab["count"]() == 0


def test_after_verify_member_bundle_tampering_denies(lab):
    def mutate():
        with sqlite3.connect(lab["outbox_member"]) as admin:
            row=admin.execute("SELECT bundle_json FROM signed_bundle_outbox").fetchone()
            pkt=json.loads(row[0])
            pkt["witness_binding"]["signature"]="AA=="
            import hashlib
            blob=json.dumps(pkt,sort_keys=True,separators=(",",":"),ensure_ascii=True)
            admin.execute(
                "UPDATE signed_bundle_outbox SET bundle_json=?,bundle_sha256=?",
                (blob,hashlib.sha256(blob.encode("ascii")).hexdigest()),
            )
    assert lab["run"](after=mutate) == (
        False,"EVIDENCE_CHANGED_AT_EFFECT_TIME_NO_GO"
    )
    assert lab["count"]() == 0


def test_after_verify_artifact_removed_denies(lab):
    assert lab["run"](after=lambda:lab["artifact"].unlink()) == (
        False,"EVIDENCE_CHANGED_AT_EFFECT_TIME_NO_GO"
    )
    assert lab["count"]()==0


def test_bad_external_receipt_pin_is_denied_before_effect(lab):
    assert lab["run"](bundle_hash="0"*64) == (
        False,"RECOVERED_RECEIPT_NOT_AUTHENTICATED_NO_GO"
    )
    assert lab["count"]()==0


def test_substituted_effect_root_denied(lab):
    assert lab["run"](effect_root=b"x"*32) == (
        False,"RECOVERED_RECEIPT_NOT_AUTHENTICATED_NO_GO"
    )


def test_wrong_required_epoch_denied_even_if_receipt_valid(lab):
    assert lab["run"](epoch=10) == (
        False,"STALE_OR_REVOKED_OWNER_AT_EFFECT_TIME_NO_GO"
    )


def test_unknown_owner_not_authorized_in_research_api(lab):
    assert lab["run"](owner="owner-b") == (
        False,"DISPOSABLE_EXECUTOR_OR_OWNER_DENIED_NO_GO"
    )


def test_competing_term_rotation_blocked_while_effect_transaction_locked(lab):
    attempts=[]
    def contender():
        attempts.append(fence.rotate(
            lab["journal"],term=5,epoch=10,owner="owner-b",revoked=True
        ))
    assert lab["run"](while_locked=contender) == (
        True,"RESEARCH_LOCAL_EFFECT_TIME_FENCE_ONLY_HOLD"
    )
    assert attempts == [False]
    assert lab["count"]()==1


def test_ordinary_revoke_after_commit_does_not_undo_prior_effect(lab):
    assert lab["run"]()[0] is True
    assert fence.rotate(lab["journal"],term=5,epoch=10,owner="owner-b",revoked=True)
    assert lab["count"]()==1
    assert lab["run"]() == (
        False,"STALE_OR_REVOKED_OWNER_AT_EFFECT_TIME_NO_GO"
    )


def test_two_concurrent_effect_consumers_only_one_succeeds(lab):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _:lab["run"](),range(2)))
    assert sum(x[0] for x in outcomes)==1
    assert lab["count"]()==1
    assert all("NO_RETRY" in reason for okay,reason in outcomes if not okay)


def test_precommit_callback_fails_without_committed_effect(lab):
    def die():
        raise RuntimeError("synthetic-before-effect-error")
    with pytest.raises(RuntimeError,match="before-effect"):
        lab["run"](while_locked=die)
    assert lab["count"]()==0
    assert lab["run"]()[0] is True


def test_missing_ack_after_commit_requires_terminal_readback_no_retry(lab):
    def disappear():
        raise RuntimeError("synthetic-lost-ack")
    with pytest.raises(fence.UncertainSyntheticEffectCommit,match="NO_RETRY"):
        lab["run"](ack=disappear)
    assert lab["count"]()==1
    assert lab["run"]()==(False,"EFFECT_ALREADY_APPLIED_NO_RETRY")


def test_wrong_or_missing_effect_db_path_denied_without_creation(lab):
    original=lab["journal"]
    original.unlink()
    assert lab["run"]()==(False,"DISPOSABLE_EXECUTOR_OR_OWNER_DENIED_NO_GO")
    assert not original.exists()


def test_symlinked_effect_db_denied(lab,tmp_path):
    moved=tmp_path/"moved.sqlite"
    lab["journal"].rename(moved)
    lab["journal"].symlink_to(moved)
    assert lab["run"]()==(False,"DISPOSABLE_EXECUTOR_OR_OWNER_DENIED_NO_GO")


def test_explicit_initialization_never_overwrites_existing_journal(lab):
    with pytest.raises(ValueError,match="bootstrap_denied"):
        fence.bootstrap(lab["journal"])


def test_privileged_sql_can_forge_effect_journal_without_executor_no_go(lab):
    # A real DBA can INSERT a fictional physical effect directly and bypass
    # both receipt verification and fenced owner transaction. This test
    # intentionally demonstrates the critical shared-admin NO-GO.
    with sqlite3.connect(lab["journal"]) as admin:
        admin.execute(
            "INSERT INTO applied VALUES(?,?,?,?,?,?,?,?)",
            (12,4,9,"owner-a","e"*32,"f"*64,"b"*64,"d"*64),
        )
    assert lab["count"]()==1
    assert lab["run"]()==(False,"EFFECT_ALREADY_APPLIED_NO_RETRY")


def test_privileged_sql_can_rewrite_revocation_and_owner_no_go(lab):
    assert fence.rotate(lab["journal"],term=5,epoch=10,owner="owner-b",revoked=True)
    with sqlite3.connect(lab["journal"]) as admin:
        admin.execute(
            "UPDATE authority SET term=4,epoch=9,owner_id='owner-a',revoked=0"
        )
    # Same-admin privileged rewind undoes term revocation; the signed
    # receipt's validity alone cannot detect that authority history rolled back.
    assert lab["run"]()[0] is True
    assert lab["count"]()==1


def test_source_contains_no_production_effect_dispatch_entrypoint():
    source=SCRIPT.read_text()
    assert "RESEARCH_ONLY_NO_REAL_EFFECT_OR_RELEASE_ENTRYPOINT" in source
    assert "release_authorized" in source and "retry_authorized" in source
    assert "network" in source
    assert "BEGIN IMMEDIATE" in source


def _child(lab, stage, opt_in=True):
    import base64
    import os
    import subprocess
    import sys

    folder = lab["journal"].parent
    request = {
        "expected": lab["expected"],
        "bundle_sha256": lab["pin"],
        "previous_head_hash": lab["baseline"]["head_hash"],
        "effect_root": base64.b64encode(lab["effect_root"]).decode("ascii"),
        "witness_root": base64.b64encode(lab["witness_root"]).decode("ascii"),
        "challenge": prior.previous.CHALLENGE,
        "now_ms": prior.previous.NOW,
    }
    (folder / "research-effect-time-request.json").write_text(json.dumps(request))
    environment = dict(os.environ)
    if opt_in:
        environment["ALLOW_SYNTHETIC_EFFECT_CHILD_KILL"] = "TEST_CHILD_ONLY"
    else:
        environment.pop("ALLOW_SYNTHETIC_EFFECT_CHILD_KILL", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "tests/fixtures/research_effect_time_child.py"),
         str(folder), stage],
        env=environment, check=False, capture_output=True, text=True, timeout=14,
    )


@pytest.mark.parametrize(("stage", "count"), [
    ("before_effect_commit", 0),
    ("after_commit_before_ack", 1),
])
def test_real_child_sigkill_proves_effect_journal_commit_boundary(lab, stage, count):
    import signal
    result = _child(lab, stage)
    assert result.returncode == -signal.SIGKILL, (result.stdout, result.stderr)
    assert f"SELF_KILL_STAGE={stage}" in result.stdout
    assert lab["count"]() == count
    with sqlite3.connect(lab["journal"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    if count:
        assert lab["run"]() == (False, "EFFECT_ALREADY_APPLIED_NO_RETRY")


def test_uncrashed_child_simulates_one_effect(lab):
    result = _child(lab, "no_crash")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert lab["count"]() == 1


def test_child_requires_explicit_opt_in(lab):
    result = _child(lab, "no_crash", opt_in=False)
    assert result.returncode == 4
    assert lab["count"]() == 0
