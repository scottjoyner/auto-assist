"""Real self-SIGKILL child and simultaneous bundle/tip SQLite transition."""
from __future__ import annotations

import base64
import concurrent.futures
import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/research-atomic-effect-bundle-outbox.py"
SPEC = importlib.util.spec_from_file_location("outbox_test_module", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
OLDER = ROOT / "tests/test_research_signed_effect_bundle.py"
SPEC2 = importlib.util.spec_from_file_location("outbox_prior_cases", OLDER)
assert SPEC2 and SPEC2.loader
prior = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(prior)
WID = prior.WID
CHALLENGE = prior.CHALLENGE
NOW = prior.NOW
CHILD = ROOT / "tests/fixtures/research_atomic_outbox_child.py"


@pytest.fixture
def state(tmp_path):
    lab = prior.lab.__wrapped__(tmp_path)
    member_dir = tmp_path / "disposable-member"
    member_dir.mkdir()
    (member_dir / ".synthetic-crash-lab-only").write_text("DISPOSABLE_ONLY\n")
    member = member_dir / "synthetic-a-owner.sqlite"
    module._member().initialize(member, witness_id=WID, baseline=lab["baseline"])
    module.bootstrap(member, WID)
    bundle = lab["assemble"]()

    def attempt(*, packet=None, callback=None, artifact=None):
        return module.issue(
            member, witness_id=WID, tip=lab["new_tip"], expected=lab["expected"],
            resource=lab["resource"], anchor=lab["anchor"],
            artifact=lab["artifact"] if artifact is None else artifact,
            bundle=bundle if packet is None else packet,
            effect_root=lab["effect_root"], witness_root=lab["witness_root"],
            challenge=CHALLENGE, now_ms=NOW, callback=callback,
        )

    def fresh():
        return module.readback(member, witness_id=WID, seq=12)

    def child(stage, optin=True):
        request = {
            "tip": lab["new_tip"], "expected": lab["expected"],
            "bundle": bundle,
            "effect_root": base64.b64encode(lab["effect_root"]).decode(),
            "witness_root": base64.b64encode(lab["witness_root"]).decode(),
            "challenge": CHALLENGE, "now_ms": NOW,
        }
        (tmp_path / "research-atomic-outbox-request.json").write_text(
            json.dumps(request)
        )
        env = dict(os.environ)
        if optin:
            env["ALLOW_SYNTHETIC_OUTBOX_KILL"] = "TEST_CHILD_ONLY"
        else:
            env.pop("ALLOW_SYNTHETIC_OUTBOX_KILL", None)
        return subprocess.run(
            [sys.executable, str(CHILD), str(tmp_path), stage],
            text=True, capture_output=True, timeout=12, env=env, check=False,
        )

    return {**lab, "outbox_member": member, "bundle": bundle,
            "attempt": attempt, "fresh": fresh, "child": child}


def test_complete_signed_bundle_persisted_atomically_with_member_tip(state):
    assert state["fresh"]()["verdict"] == "OUTBOX_NOT_COMMITTED_HOLD"
    assert state["attempt"]() == (True, "RESEARCH_ATOMIC_OUTBOX_OBSERVED_HOLD")
    outcome = state["fresh"]()
    assert outcome["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
    assert outcome["bundle"] == state["bundle"]
    assert outcome["retry_authorized"] is False
    assert outcome["release_authorized"] is False
    assert state["check"](outcome["bundle"])[0] is True


@pytest.mark.parametrize(("stage", "status", "count"), [
    ("after_outbox_insert_before_commit", "OUTBOX_NOT_COMMITTED_HOLD", 0),
    ("after_commit_before_ack", "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD", 1),
])
def test_real_process_sigkill_atomic_issuance_and_bundle(state, stage, status, count):
    child = state["child"](stage)
    assert child.returncode == -signal.SIGKILL, (child.stdout, child.stderr)
    assert "SYNTHETIC_KILL_STAGE="+stage in child.stdout
    result = state["fresh"]()
    assert result["verdict"] == status
    assert result["release_authorized"] is False
    assert result["retry_authorized"] is False
    with sqlite3.connect(state["outbox_member"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM issued").fetchone() == (count,)
        assert db.execute("SELECT count(*) FROM signed_bundle_outbox").fetchone() == (count,)
    if count:
        assert result["bundle"] == state["bundle"]
        assert state["check"](result["bundle"])[0] is True
        assert state["attempt"]()[1] == "OUTBOX_ALREADY_CONSUMED_OR_TERM_DENIED_NO_GO"


def test_uncertainty_exception_after_commit_never_reissues(state):
    def fail(stage):
        if stage == "after_commit_before_ack":
            raise RuntimeError("lost ACK")

    with pytest.raises(module.UncertainOutboxCommit, match="NO_RETRY"):
        state["attempt"](callback=fail)
    assert state["fresh"]()["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
    assert not state["attempt"]()[0]


def test_precommit_error_rolls_back_tip_issuance_and_bundle(state):
    def fail(stage):
        if stage == "after_outbox_insert_before_commit":
            raise RuntimeError("pre-COMMIT failure")

    with pytest.raises(RuntimeError, match="pre-COMMIT"):
        state["attempt"](callback=fail)
    assert state["fresh"]()["verdict"] == "OUTBOX_NOT_COMMITTED_HOLD"


def test_normal_child_with_no_crash_proves_terminal_bundle(state):
    child = state["child"]("no_crash")
    assert child.returncode == 0, (child.stdout, child.stderr)
    assert state["fresh"]()["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"


def test_default_child_refuses_without_optin(state):
    child = state["child"]("no_crash", optin=False)
    assert child.returncode == 4
    assert state["fresh"]()["verdict"] == "OUTBOX_NOT_COMMITTED_HOLD"


def test_two_same_member_concurrent_issuers_only_one_wins(state):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: state["attempt"](), range(2)))
    assert sum(x[0] for x in attempts) == 1
    assert state["fresh"]()["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"


def test_invalid_bundle_never_advances(state):
    fake = json.loads(json.dumps(state["bundle"]))
    fake["effect_attestation"]["claim"]["artifact_sha256"] = "0" * 64
    assert state["attempt"](packet=fake) == (
        False, "OUTBOX_SIGNED_EFFECT_VERIFICATION_DENIED_NO_GO"
    )
    assert state["fresh"]()["verdict"] == "OUTBOX_NOT_COMMITTED_HOLD"


def test_missing_artifact_denies_before_transition(state):
    state["artifact"].unlink()
    assert state["attempt"]()[0] is False
    assert state["fresh"]()["verdict"] == "OUTBOX_NOT_COMMITTED_HOLD"


def test_direct_sql_can_forge_ledger_plus_complete_bundle_no_go(state):
    # This trial does NOT claim host/DBA independence. Admin can edit the
    # durable ledger even though member SQLite CAS normally serializes it.
    assert state["attempt"]()[0] is True
    with sqlite3.connect(state["outbox_member"]) as admin:
        admin.execute("UPDATE tip SET commit_seq=99")
    assert state["fresh"]()["verdict"] == "OUTBOX_LEDGER_CONTRADICTION_NO_GO"
    # A host admin who can restore the whole member DB, signing roots and
    # source/anchor evidence could make an old complete packet appear current.


def test_delete_outbox_row_with_committed_tip_detected(state):
    assert state["attempt"]()[0] is True
    with sqlite3.connect(state["outbox_member"]) as admin:
        admin.execute("DELETE FROM signed_bundle_outbox")
    assert state["fresh"]()["verdict"] == "OUTBOX_LEDGER_CONTRADICTION_NO_GO"


def test_corrupted_signed_packet_blob_detected(state):
    assert state["attempt"]()[0] is True
    with sqlite3.connect(state["outbox_member"]) as admin:
        admin.execute("UPDATE signed_bundle_outbox SET bundle_json='{}'")
    assert state["fresh"]()["verdict"] == "OUTBOX_LEDGER_CONTRADICTION_NO_GO"


def test_schema_bootstrap_refuses_nonempty_history(state):
    assert state["attempt"]()[0] is True
    with pytest.raises(ValueError):
        module.bootstrap(state["outbox_member"], WID)


def test_research_only_no_release_entrypoint():
    source = SCRIPT.read_text()
    assert "RESEARCH_ONLY_NO_OUTBOX_DELIVERY_OR_RELEASE_ENTRYPOINT" in source
    assert "BEGIN IMMEDIATE" in source
    assert "Ed25519PrivateKey" not in source
    assert "release_authorized" in source and "retry_authorized" in source


def test_full_member_snapshot_restore_erases_committed_bundle_no_go(state, tmp_path):
    import shutil

    snapshot = tmp_path / "pre-outbox-issuance.sqlite"
    shutil.copy2(state["outbox_member"], snapshot)
    assert state["attempt"]()[0] is True
    assert state["fresh"]()["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
    shutil.copy2(snapshot, state["outbox_member"])
    # The local SQL ledger now looks like COMMIT was never attempted.
    # The caller must not infer 'safe to reissue' from this readback.
    result = state["fresh"]()
    assert result["verdict"] == "OUTBOX_NOT_COMMITTED_HOLD"
    assert not result["retry_authorized"]


def test_shared_admin_false_anchor_commit_can_store_complete_bundle_no_go(tmp_path):
    # Fabricated anchor rows, synthetic file and TWO signing keys controlled
    # by one operator can produce an internally valid complete outbox record.
    forged_dir = tmp_path / "fabricated"
    forged_dir.mkdir()
    faux = prior.prior.lab.__wrapped__(forged_dir)
    prior.prior.fake_sql_anchor_commit_without_authorized_cas(faux)
    prior.prior.gate.create_disposable_effect(faux["artifact"], prior.prior.PAYLOAD)
    ok, _, cert = faux["issue_effect"]()
    assert ok
    # Rebuild a fully cross-signed bundle from a separate synthetic key owner.
    real_dir = tmp_path / "authorized-bundle-template"
    real_dir.mkdir()
    real = prior.lab.__wrapped__(real_dir)
    bundle = real["assemble"](cert_override=cert, witness=faux["keys"][WID])
    member_dir = forged_dir / "disposable-member"
    member_dir.mkdir()
    (member_dir / ".synthetic-crash-lab-only").write_text("DISPOSABLE_ONLY\n")
    fresh_member = member_dir / "synthetic-a-owner.sqlite"
    module._member().initialize(
        fresh_member, witness_id=WID, baseline=faux["baseline"]
    )
    module.bootstrap(fresh_member, WID)
    result = module.issue(
        fresh_member, witness_id=WID, tip=faux["new_tip"],
        expected=faux["expected"], resource=faux["resource"],
        anchor=faux["anchor"], artifact=faux["artifact"],
        bundle=bundle, effect_root=real["effect_root"],
        witness_root=faux["roots"][WID]["public"],
        challenge=CHALLENGE, now_ms=NOW,
    )
    assert result == (True, "RESEARCH_ATOMIC_OUTBOX_OBSERVED_HOLD")
    assert module.readback(
        fresh_member, witness_id=WID, seq=12
    )["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
    # No authorized anchor-CAS executed. Atomicity != effect provenance.
