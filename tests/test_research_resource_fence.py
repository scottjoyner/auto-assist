"""Synthetic resource SQLite write-lock held across separate anchor CAS.

The source path and anchor path both reside under pytest tmp_path.
Tests that *succeed at bypassing* protections explicitly document NO-GOs.
"""
from __future__ import annotations

import base64
import importlib.util
import os
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/research-resource-fence-around-anchor.py"
spec = importlib.util.spec_from_file_location("resource_fence_research", SOURCE)
assert spec and spec.loader
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
consumer = lab._consumer()
verifier = consumer._verifier()
NOW = 1792000000000
KEY_ID = "independent-resource-commit-research-v1"
RESOURCE_ID = "auto-router-runtime-projection"


@pytest.fixture
def scenario(tmp_path):
    resource = tmp_path / "resource.sqlite"
    anchor = tmp_path / "anchor.sqlite"
    consumer.initialize_disposable_anchor(anchor)
    priv = Ed25519PrivateKey.generate()
    roots = {KEY_ID: priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )}
    effect = {
        "resource_id": RESOURCE_ID, "term": 4, "commit_seq": 12,
        "generation": 27, "checksum": "b" * 64,
        "revision_sha256": "d" * 64, "committed": True,
    }
    receipt = {
        "schema_version": 1, "audience": "synthetic-resource-commit-observation",
        **{k: effect[k] for k in (
            "resource_id", "term", "commit_seq", "generation",
            "checksum", "revision_sha256",
        )},
        "nonce": "e" * 32, "challenge": "a" * 64,
        "key_epoch": 7, "issued_at_ms": NOW - 1000,
        "expires_at_ms": NOW + 10000,
    }
    with sqlite3.connect(resource) as con:
        con.execute("""CREATE TABLE resource_effect (
           resource_id TEXT PRIMARY KEY, term INTEGER, commit_seq INTEGER,
           generation INTEGER, checksum TEXT, revision_sha256 TEXT,
           committed INTEGER)""")
        con.execute("""CREATE TABLE resource_commit_receipts (
           term INTEGER, commit_seq INTEGER PRIMARY KEY, generation INTEGER,
           checksum TEXT, revision_sha256 TEXT, nonce TEXT)""")
        con.execute(
            "INSERT INTO resource_effect VALUES (?,?,?,?,?,?,?)",
            (RESOURCE_ID, 4, 12, 27, "b" * 64, "d" * 64, 1),
        )
        con.execute(
            "INSERT INTO resource_commit_receipts VALUES (?,?,?,?,?,?)",
            (4, 12, 27, "b" * 64, "d" * 64, "e" * 32),
        )

    def sign(claim=None):
        r = deepcopy(receipt if claim is None else claim)
        return {
            "key_id": KEY_ID, "receipt": r,
            "signature": base64.b64encode(priv.sign(verifier.canonical(r))).decode(),
        }

    def invoke(
        *, envelope=None, expected=None, before=None, after=None,
        source=None, previous=None,
    ):
        return lab.consume_while_source_fenced(
            resource if source is None else source, anchor,
            sign() if envelope is None else envelope,
            pinned_public_keys=roots,
            expected_effect=effect if expected is None else expected,
            expected_challenge="a" * 64,
            expected_previous=(
                {"term": 4, "commit_seq": 11, "last_nonce": "c" * 32}
                if previous is None else previous
            ),
            now_ms=NOW, while_source_locked=before,
            after_anchor_committed=after,
        )

    return {
        "resource": resource, "anchor": anchor, "roots": roots,
        "effect": effect, "receipt": receipt, "sign": sign, "invoke": invoke,
    }


def test_actual_resource_transaction_fences_normal_concurrent_writer(scenario):
    s = scenario
    attempts = []

    def attacker():
        with sqlite3.connect(s["resource"], timeout=0.02) as other:
            other.execute("PRAGMA busy_timeout=20")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute(
                    "UPDATE resource_effect SET generation=999 WHERE resource_id=?",
                    (RESOURCE_ID,),
                )
        attempts.append("DENIED_DB_LOCK")

    assert s["invoke"](before=attacker) == (
        True, "RESEARCH_SOURCE_WRITE_LOCK_AND_ANCHOR_CAS_ONLY"
    )
    assert attempts == ["DENIED_DB_LOCK"]
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 12
    assert lab._committed_readback(s["resource"], "e" * 32) == s["effect"]


def test_source_write_lock_held_even_after_anchor_commit_until_release(scenario):
    s = scenario
    locked = []

    def after():
        with sqlite3.connect(s["resource"], timeout=0.02) as other:
            other.execute("PRAGMA busy_timeout=20")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE resource_effect SET generation=98")
        locked.append(True)

    assert s["invoke"](after=after)[0] is True
    assert locked == [True]


def test_missing_source_cannot_be_silently_created(scenario):
    s = scenario
    s["resource"].unlink()
    assert s["invoke"]() == (False, "source_resource_unavailable_no_authority")
    assert not s["resource"].exists()
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 11


def test_missing_matching_source_receipt_denies_anchor(scenario):
    s = scenario
    with sqlite3.connect(s["resource"]) as con:
        con.execute("DELETE FROM resource_commit_receipts")
    assert s["invoke"]() == (False, "source_committed_effect_or_receipt_missing")
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 11


def test_changed_source_receipt_nonce_denies_anchor(scenario):
    s = scenario
    with sqlite3.connect(s["resource"]) as con:
        con.execute("UPDATE resource_commit_receipts SET nonce=?", ("f" * 32,))
    assert s["invoke"]() == (False, "source_committed_effect_or_receipt_missing")
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 11


def test_wrong_generation_effect_denies_anchor(scenario):
    s = scenario
    with sqlite3.connect(s["resource"]) as con:
        con.execute("UPDATE resource_effect SET generation=123")
    assert s["invoke"]() == (False, "source_committed_effect_or_receipt_missing")


def test_missing_anchor_does_not_change_source(scenario):
    s = scenario
    s["anchor"].unlink()
    assert s["invoke"]() == (False, "anchor_unavailable_no_authority")
    assert lab._committed_readback(s["resource"], "e" * 32) == s["effect"]


def test_after_anchor_callback_failure_marks_uncertainty_no_retry(scenario):
    s = scenario

    def crash_after():
        raise OSError("synthetic crash AFTER anchor COMMIT")

    with pytest.raises(
        consumer.UncertainReceiptCommit, match="OUTCOME_UNCERTAIN_NO_RETRY"
    ):
        s["invoke"](after=crash_after)
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 12
    assert s["invoke"]() == (False, "stale_or_restored_anchor_cas")


def test_unexpected_pre_fence_error_never_advances_anchor(scenario):
    s = scenario

    def crash_before():
        raise RuntimeError("synthetic pre-CAS error")

    with pytest.raises(RuntimeError):
        s["invoke"](before=crash_before)
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 11


def test_privileged_source_writer_can_mutate_after_lock_release_no_go(scenario):
    s = scenario
    assert s["invoke"]()[0] is True
    with sqlite3.connect(s["resource"]) as privileged:
        privileged.execute("UPDATE resource_effect SET generation=777")
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 12
    assert lab._committed_readback(s["resource"], "e" * 32) is None


def test_colluding_signer_and_source_dba_can_fabricate_both_rows_no_go(scenario):
    s = scenario
    forged = dict(s["effect"], generation=987)
    forged_receipt = dict(s["receipt"], generation=987)
    with sqlite3.connect(s["resource"]) as privileged:
        privileged.execute("UPDATE resource_effect SET generation=987")
        privileged.execute("UPDATE resource_commit_receipts SET generation=987")
    assert s["invoke"](
        expected=forged, envelope=s["sign"](forged_receipt)
    ) == (True, "RESEARCH_SOURCE_WRITE_LOCK_AND_ANCHOR_CAS_ONLY")


def test_fenced_connection_does_not_secure_path_against_admin_replacement_no_go(
    scenario, tmp_path,
):
    s = scenario
    substitute = tmp_path / "substitute.sqlite"
    # The "attacker" prepares an alternate SQLite database with coherent
    # fabricated source receipt, then swaps the pathname as an OS admin.
    with sqlite3.connect(substitute) as con:
        con.execute("""CREATE TABLE resource_effect (
           resource_id TEXT PRIMARY KEY,term INTEGER,commit_seq INTEGER,
           generation INTEGER,checksum TEXT,revision_sha256 TEXT,
           committed INTEGER)""")
        con.execute("""CREATE TABLE resource_commit_receipts (
           term INTEGER,commit_seq INTEGER PRIMARY KEY,generation INTEGER,
           checksum TEXT,revision_sha256 TEXT,nonce TEXT)""")
        con.execute(
            "INSERT INTO resource_effect VALUES (?,?,?,?,?,?,?)",
            (RESOURCE_ID, 4, 12, 888, "b" * 64, "d" * 64, 1),
        )
        con.execute(
            "INSERT INTO resource_commit_receipts VALUES (?,?,?,?,?,?)",
            (4, 12, 888, "b" * 64, "d" * 64, "e" * 32),
        )
    forged = dict(s["effect"], generation=888)
    claim = dict(s["receipt"], generation=888)
    def replace_under_lock():
        os.replace(substitute, s["resource"])

    assert s["invoke"](
        expected=forged, envelope=s["sign"](claim), before=replace_under_lock
    ) == (True, "RESEARCH_SOURCE_WRITE_LOCK_AND_ANCHOR_CAS_ONLY")
    assert consumer.read_anchor(s["anchor"])["commit_seq"] == 12
    # SQLite locks the old inode; it doesn't authenticate future pathnames.
    assert lab._committed_readback(s["resource"], "e" * 32)["generation"] == 888


def test_research_module_has_no_real_release_path():
    source = SOURCE.read_text()
    assert "RESEARCH_ONLY_NO_FENCED_RESOURCE_OR_RELEASE_ENTRYPOINT" in source
    assert "BEGIN IMMEDIATE" in source
    assert "mode=rw" in source and "mode=ro" in source
