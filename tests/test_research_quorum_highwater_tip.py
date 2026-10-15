"""2-of-3 research checkpoint signatures and hash links; custody still unproven.

All keys are controlled by the SAME pytest process. Multi-signatures cannot
establish independent administration, liveness of latest tip or anti-rollback.
"""
from __future__ import annotations

import base64
import importlib.util
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts/research-quorum-highwater-tip.py"
SPEC = importlib.util.spec_from_file_location("quorum_research", SRC)
assert SPEC and SPEC.loader
quorum = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quorum)
CRASH_SRC = ROOT / "tests/test_research_receipt_subprocess_crash_matrix.py"
SPEC2 = importlib.util.spec_from_file_location("quorum_real_crash", CRASH_SRC)
assert SPEC2 and SPEC2.loader
crash = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(crash)
FLOOR_SRC = ROOT / "tests/test_research_external_signed_highwater_floor.py"
SPEC3 = importlib.util.spec_from_file_location("quorum_expected_claim", FLOOR_SRC)
assert SPEC3 and SPEC3.loader
floor = importlib.util.module_from_spec(SPEC3)
SPEC3.loader.exec_module(floor)
NOW = 1792000000000
CHALLENGE = "f" * 64
IDS = ("a-owner", "b-archive", "c-reviewer")


@pytest.fixture
def lab(tmp_path):
    resource = tmp_path / "research-resource.sqlite"
    anchor = tmp_path / "research-anchor.sqlite"
    (tmp_path / ".synthetic-crash-lab-only").write_text("DISPOSABLE_ONLY\n")
    floor.terminal.consumer.initialize_disposable_anchor(anchor)
    with sqlite3.connect(resource) as db:
        db.execute("""CREATE TABLE effect (
          resource_id TEXT PRIMARY KEY,term INTEGER,commit_seq INTEGER,
          generation INTEGER,checksum TEXT,revision_sha256 TEXT,
          committed INTEGER)""")
        db.execute("""CREATE TABLE receipt (
          term INTEGER,commit_seq INTEGER PRIMARY KEY,generation INTEGER,
          checksum TEXT,revision_sha256 TEXT,nonce TEXT)""")
    keys = {ident: Ed25519PrivateKey.generate() for ident in IDS}
    roots = {
        ident: {
            "public": key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw,
            ),
            "epoch": 3,
        }
        for ident, key in keys.items()
    }
    expected = floor.terminal.expected()
    base_tip = {
        "resource_id": expected["resource_id"],
        "term": expected["term"],
        "commit_seq": expected["previous_seq"],
        "last_nonce": expected["previous_nonce"],
        "receipt_digest": "0" * 64,
        "prev_hash": "9" * 64,
    }
    base_tip["head_hash"] = quorum.link(base_tip)
    baseline = {k: base_tip[k] for k in (
        "resource_id", "term", "commit_seq",
        "last_nonce", "receipt_digest", "head_hash",
    )}
    committed_tip = {
        "resource_id": expected["resource_id"],
        "term": expected["term"],
        "commit_seq": expected["commit_seq"],
        "last_nonce": expected["nonce"],
        "receipt_digest": expected["receipt_digest"],
        "prev_hash": baseline["head_hash"],
    }
    committed_tip["head_hash"] = quorum.link(committed_tip)

    def sign(ident, tip=None, *, key=None, **changes):
        record = {
            "schema_version": 1, "witness_id": ident,
            "key_epoch": 3, "challenge": CHALLENGE,
            "issued_at_ms": NOW - 50,
            "expires_at_ms": NOW + 10_000,
            "tip": deepcopy(committed_tip if tip is None else tip),
        }
        record.update(changes)
        private = keys[ident] if key is None else key
        return {
            **record,
            "signature": base64.b64encode(
                private.sign(quorum.canonical(record))
            ).decode("ascii"),
        }

    def evaluate(certs=None, *, pinned=None, base=None, fresh=CHALLENGE):
        return quorum.assess(
            resource, anchor, expected=expected,
            signed_heads=([sign(IDS[0]), sign(IDS[1])] if certs is None else certs),
            pinned_roots=roots if pinned is None else pinned,
            baseline=baseline if base is None else base,
            fresh_challenge=fresh, now_ms=NOW,
        )

    def run(stage):
        child = crash.child(resource, anchor, stage)
        if stage == "no_crash":
            assert child.returncode == 0, (child.stdout, child.stderr)
        else:
            assert child.returncode == -crash.signal.SIGKILL, (
                child.stdout, child.stderr
            )

    return {
        "resource": resource, "anchor": anchor,
        "roots": roots, "keys": keys, "expected": expected,
        "baseline": baseline, "old_tip": base_tip,
        "new_tip": committed_tip, "sign": sign,
        "evaluate": evaluate, "crash": run,
    }


def verdict(lab, certs=None, **kwargs):
    result = lab["evaluate"](certs, **kwargs)
    assert result["release_authorized"] is False
    assert result["retry_authorized"] is False
    return result["verdict"]


@pytest.mark.parametrize(("stage", "outcome"), [
    ("source_pre_commit", "QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO"),
    ("source_after_commit_before_anchor", "QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO"),
    ("anchor_after_updates_before_commit", "QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO"),
    ("anchor_after_commit_before_ack", "ANCHOR_CONSUMED_OBSERVED_HOLD"),
    ("no_crash", "ANCHOR_CONSUMED_OBSERVED_HOLD"),
])
def test_quorum_current_tip_vs_real_crash_states(lab, stage, outcome):
    lab["crash"](stage)
    assert verdict(lab) == outcome


def test_two_matching_signatures_freshly_observe_current_history(lab):
    lab["crash"]("no_crash")
    assert verdict(lab) == "ANCHOR_CONSUMED_OBSERVED_HOLD"


def test_single_signed_witness_does_not_make_quorum(lab):
    lab["crash"]("no_crash")
    assert verdict(lab, [lab["sign"](IDS[0])]) == "WITNESS_QUORUM_UNAVAILABLE_NO_GO"


def test_duplicate_witness_identity_cannot_count_twice(lab):
    lab["crash"]("no_crash")
    cert = lab["sign"](IDS[0])
    assert verdict(lab, [cert, deepcopy(cert)]) == "DUPLICATE_WITNESS_IDENTITY_NO_GO"


def test_one_current_and_one_old_signed_tip_is_fail_closed_fork(lab):
    lab["crash"]("no_crash")
    certs = [lab["sign"](IDS[0]), lab["sign"](IDS[1], lab["old_tip"])]
    assert verdict(lab, certs) == "WITNESS_TIP_FORK_OR_STALE_CONFLICT_NO_GO"


def test_two_current_one_stale_witness_still_fails_closed(lab):
    lab["crash"]("no_crash")
    certs = [
        lab["sign"](IDS[0]), lab["sign"](IDS[1]),
        lab["sign"](IDS[2], lab["old_tip"]),
    ]
    assert verdict(lab, certs) == "WITNESS_TIP_FORK_OR_STALE_CONFLICT_NO_GO"


def test_two_current_one_absent_witness_is_observation_hold(lab):
    lab["crash"]("no_crash")
    assert verdict(lab, [
        lab["sign"](IDS[0]), lab["sign"](IDS[2]),
    ]) == "ANCHOR_CONSUMED_OBSERVED_HOLD"


def test_two_current_signed_tips_detect_restored_anchor(lab, tmp_path):
    saved = tmp_path / "prior-anchor.sqlite"
    shutil.copy2(lab["anchor"], saved)
    lab["crash"]("no_crash")
    shutil.copy2(saved, lab["anchor"])
    assert verdict(lab) == "QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO"


def test_signed_old_quorum_replay_hides_restore_no_go(lab, tmp_path):
    saved = tmp_path / "prior-anchor.sqlite"
    shutil.copy2(lab["anchor"], saved)
    old = lab["old_tip"]
    old_certs = [lab["sign"](IDS[0], old), lab["sign"](IDS[1], old)]
    lab["crash"]("no_crash")
    assert verdict(lab) == "ANCHOR_CONSUMED_OBSERVED_HOLD"
    shutil.copy2(saved, lab["anchor"])
    assert verdict(lab, old_certs) == "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"
    # A 2-of-3 signature quorum does NOT attest it is the latest quorum tip.


def test_two_colluding_old_signers_can_freshly_hide_restore_no_go(lab, tmp_path):
    saved = tmp_path / "prior-anchor.sqlite"
    shutil.copy2(lab["anchor"], saved)
    lab["crash"]("no_crash")
    shutil.copy2(saved, lab["anchor"])
    old = lab["old_tip"]
    assert verdict(lab, [
        lab["sign"](IDS[0], old), lab["sign"](IDS[1], old),
    ]) == "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"


def test_pinned_newer_baseline_detects_downgraded_quorum_no_go(lab, tmp_path):
    saved = tmp_path / "prior-anchor.sqlite"
    shutil.copy2(lab["anchor"], saved)
    lab["crash"]("no_crash")
    shutil.copy2(saved, lab["anchor"])
    newest = {k: lab["new_tip"][k] for k in lab["baseline"]}
    certs = [
        lab["sign"](IDS[0], lab["old_tip"]),
        lab["sign"](IDS[1], lab["old_tip"]),
    ]
    assert verdict(lab, certs, base=newest) == "WITNESS_OLD_OR_NONADJACENT_HEAD_NO_GO"


def test_one_unpinned_substitute_key_cannot_forge_a_quorum(lab):
    attacker = Ed25519PrivateKey.generate()
    forged = lab["sign"](IDS[0], key=attacker)
    assert verdict(lab, [forged, lab["sign"](IDS[1])]) == "WITNESS_SIGNATURE_INVALID_NO_GO"


def test_root_map_substitution_for_two_signers_remains_no_go(lab, tmp_path):
    saved = tmp_path / "prior-anchor.sqlite"
    shutil.copy2(lab["anchor"], saved)
    lab["crash"]("no_crash")
    shutil.copy2(saved, lab["anchor"])
    fake_keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    roots = deepcopy(lab["roots"])
    for id_, key in zip(IDS[:2], fake_keys, strict=True):
        roots[id_]["public"] = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
    forged = [
        lab["sign"](IDS[0], lab["old_tip"], key=fake_keys[0]),
        lab["sign"](IDS[1], lab["old_tip"], key=fake_keys[1]),
    ]
    assert verdict(lab, forged, pinned=roots) == "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"


def test_any_validly_signed_same_seq_fork_denied_even_with_new_digest(lab):
    lab["crash"]("no_crash")
    alternate = deepcopy(lab["new_tip"])
    alternate["receipt_digest"] = "e" * 64
    alternate["head_hash"] = quorum.link(alternate)
    assert verdict(lab, [
        lab["sign"](IDS[0]), lab["sign"](IDS[1], alternate),
    ]) == "WITNESS_TIP_FORK_OR_STALE_CONFLICT_NO_GO"


def test_broken_hash_chain_denied_even_if_signatures_are_valid(lab):
    tip = deepcopy(lab["new_tip"])
    tip["prev_hash"] = "0" * 64
    tip["head_hash"] = quorum.link(tip)
    assert verdict(lab, [
        lab["sign"](IDS[0], tip), lab["sign"](IDS[1], tip),
    ]) == "WITNESS_HASH_CHAIN_DISCONTINUITY_NO_GO"


def test_signed_hash_field_tamper_denied(lab):
    tip = deepcopy(lab["new_tip"])
    tip["head_hash"] = "0" * 64
    assert verdict(lab, [
        lab["sign"](IDS[0], tip), lab["sign"](IDS[1], tip),
    ]) == "WITNESS_HASH_CHAIN_TIP_INVALID_NO_GO"


def test_receipt_digest_conflict_denied(lab):
    tip = deepcopy(lab["new_tip"])
    tip["receipt_digest"] = "e" * 64
    tip["head_hash"] = quorum.link(tip)
    assert verdict(lab, [
        lab["sign"](IDS[0], tip), lab["sign"](IDS[1], tip),
    ]) == "WITNESS_EFFECT_DIGEST_CONFLICT_NO_GO"


@pytest.mark.parametrize(("cert_changes", "reason"), [
    ({"challenge": "0" * 64}, "WITNESS_CERTIFICATE_NOT_FRESH_NO_GO"),
    ({"key_epoch": 99}, "WITNESS_CERTIFICATE_NOT_FRESH_NO_GO"),
    ({"expires_at_ms": NOW - 1}, "WITNESS_CERTIFICATE_NOT_FRESH_NO_GO"),
    ({"issued_at_ms": NOW + 1}, "WITNESS_CERTIFICATE_NOT_FRESH_NO_GO"),
    ({"schema_version": True}, "WITNESS_CERTIFICATE_MALFORMED_NO_GO"),
])
def test_signed_wrong_witness_metadata_denied(lab, cert_changes, reason):
    certs = [lab["sign"](IDS[0], **cert_changes), lab["sign"](IDS[1])]
    assert verdict(lab, certs) == reason


def test_mutation_between_readback_passes_denied(lab, monkeypatch):
    lab["crash"]("source_after_commit_before_anchor")
    local = quorum._local()
    orig = local._read_snapshot
    calls = 0

    def mutate_once(*args):
        nonlocal calls
        result = orig(*args)
        calls += 1
        if calls == 1:
            with sqlite3.connect(lab["anchor"]) as db:
                db.execute("UPDATE highwater SET commit_seq=13")
        return result

    monkeypatch.setattr(local, "_read_snapshot", mutate_once)
    assert verdict(lab) == "CHANGED_BETWEEN_READBACKS_NO_GO"


def test_all_outputs_are_denials_even_if_local_witnesses_match(lab):
    lab["crash"]("no_crash")
    result = lab["evaluate"]()
    assert result == {
        "schema_version": 1,
        "release_authorized": False, "retry_authorized": False,
        "verdict": "ANCHOR_CONSUMED_OBSERVED_HOLD",
    }
    assert "Ed25519PrivateKey" not in SRC.read_text()
    assert "NO_QUORUM_AUTHORITY_OR_RELEASE_ENTRYPOINT" in SRC.read_text()


def test_signed_quorum_ahead_of_uncommitted_anchor_is_not_restore_proof_no_go(lab):
    # This is an ordinary source-only COMMIT: anchor NEVER reached seq12.
    # Two cooperative signers may nevertheless publish seq12. Local result
    # says "restore" even though the actual history was only pending. An
    # authenticated quorum is not a resource-owned anchor COMMIT witness.
    lab["crash"]("source_after_commit_before_anchor")
    assert verdict(lab) == "QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO"


def test_explicit_key_epoch_rotation_rejects_old_quorum(lab):
    lab["crash"]("no_crash")
    rotated = deepcopy(lab["roots"])
    rotated[IDS[0]]["epoch"] = 4
    assert verdict(lab, [
        lab["sign"](IDS[0]), lab["sign"](IDS[1]),
    ], pinned=rotated) == "WITNESS_CERTIFICATE_NOT_FRESH_NO_GO"


def test_unrecognized_witness_not_admitted_into_member_set(lab):
    foreign = deepcopy(lab["sign"](IDS[0]))
    foreign["witness_id"] = "unknown-operator"
    assert verdict(lab, [
        foreign, lab["sign"](IDS[1]),
    ]) == "WITNESS_CERTIFICATE_MALFORMED_NO_GO"


def test_one_invalid_signature_forces_denial_even_if_two_valid(lab):
    lab["crash"]("no_crash")
    forged = lab["sign"](IDS[2])
    forged["signature"] = "AA=="
    assert verdict(lab, [
        lab["sign"](IDS[0]), lab["sign"](IDS[1]), forged,
    ]) == "WITNESS_SIGNATURE_INVALID_NO_GO"


def test_quorum_config_change_is_not_allowed_in_verifier(lab):
    assert quorum.assess(
        lab["resource"], lab["anchor"], expected=lab["expected"],
        signed_heads=[lab["sign"](IDS[0]), lab["sign"](IDS[1])],
        pinned_roots=lab["roots"], baseline=lab["baseline"],
        fresh_challenge=CHALLENGE, now_ms=NOW, required_quorum=1,
    )["verdict"] == "QUORUM_CUSTODY_OR_CHALLENGE_MISSING_NO_GO"
