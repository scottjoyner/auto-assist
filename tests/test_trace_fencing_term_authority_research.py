"""No-network fail-closed fencing negative tests. NO quorum/release approval.

A local checkpoint detects only *uncoordinated* DB rollback. Two cloned
authorities + checkpoints can split brain: the negative is EXPECTED.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trace_fencing_term_authority_research import (
    FenceDenied, ResearchFencingAuthority, _canon, make_receipt,
    make_takeover_request
)

SERVER = "disposable-neo526-instance-1"
GENERATION = "first-startup-generation"
TXID = "neo4j-transaction-77"


def rig(tmp_path, capacity=1):
    signer = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    operator = Ed25519PrivateKey.generate()
    operator_public = operator.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    db_path = tmp_path / "pg-independent-research-fence.sqlite"
    anchor = tmp_path / "term-checkpoint.json"
    authority = ResearchFencingAuthority.bootstrap(
        db_path, anchor, "old-gateway", capacity, public, operator_public)
    return authority, signer, db_path, anchor, operator


def witness(authority, signer, attempt, owner="old-gateway", term=1,
            generation=GENERATION, txid=TXID):
    receipt = make_receipt(
        authority.snapshot(), owner, term, attempt, SERVER, generation, txid
    )
    return receipt, signer.sign(_canon(receipt))


def approval(authority, signer, new_owner):
    body = make_takeover_request(authority.snapshot(), new_owner)
    return body, signer.sign(_canon(body))


def test_admission_and_takeover_refuse_unresolved_capacity(tmp_path):
    auth, signer, _, _, operator = rig(tmp_path)
    slot = auth.admit("old-gateway", 1, "operation-1")
    assert auth.snapshot()["pending"] == 1
    with pytest.raises(FenceDenied, match="UNCERTAIN_INFLIGHT"):
        auth.takeover("successor", 1, *approval(auth, operator, "successor"))
    with pytest.raises(FenceDenied, match="PENDING_PHYSICAL_CAPACITY"):
        auth.admit("old-gateway", 1, "operation-2")
    with pytest.raises(FenceDenied, match="STALE_FENCING_TERM"):
        auth.admit("successor", 2, "unauthorized")
    auth.bind("old-gateway", 1, slot, SERVER, GENERATION, TXID)
    auth.quarantine(slot)
    with pytest.raises(FenceDenied, match="UNCERTAIN_INFLIGHT"):
        auth.takeover("successor", 1, *approval(auth, operator, "successor"))
    body, signature = witness(auth, signer, slot)
    auth.close(body, signature)
    assert auth.snapshot()["pending"] == 0
    assert auth.takeover("successor", 1, *approval(auth, operator, "successor")) == 2
    with pytest.raises(FenceDenied, match="STALE_FENCING_TERM"):
        auth.admit("old-gateway", 1, "stale")
    assert auth.admit("successor", 2, "new-work")


def test_successful_driver_response_does_not_automatically_free_slot(tmp_path):
    auth, _, _, _, operator = rig(tmp_path)
    slot = auth.admit("old-gateway", 1, "operation")
    auth.bind("old-gateway", 1, slot, SERVER, GENERATION, TXID)
    # No release via timeout/TTL or return value.
    assert auth.snapshot()["pending"] == 1
    with pytest.raises(FenceDenied, match="UNCERTAIN_INFLIGHT"):
        auth.takeover("next-owner", 1, *approval(auth, operator, "next-owner"))


def test_reserved_before_graph_starts_quarantines_takeover(tmp_path):
    auth, signer, _, _, operator = rig(tmp_path)
    slot = auth.admit("old-gateway", 1, "prebolt")
    with pytest.raises(FenceDenied, match="UNCERTAIN_INFLIGHT"):
        auth.takeover("new", 1, *approval(auth, operator, "new"))
    body, sig = witness(auth, signer, slot)
    with pytest.raises(FenceDenied, match="WITNESS_ATTEMPT_MISMATCH"):
        auth.close(body, sig)
    auth.quarantine(slot)
    with pytest.raises(FenceDenied, match="WITNESS_ATTEMPT_MISMATCH"):
        auth.close(body, sig)


@pytest.mark.parametrize("alter", [
    lambda body: dict(body, term=2),
    lambda body: dict(body, server="other-server"),
    lambda body: dict(body, generation="old-image"),
    lambda body: dict(body, txid="neo4j-transaction-78"),
    lambda body: dict(body, observation="client-timeout"),
    lambda body: dict(body, attempt_id="other-attempt"),
    lambda body: dict(body, instance="restored-instance"),
])
def test_tampered_or_confused_closure_cannot_free_capacity(tmp_path, alter):
    auth, signer, _, _, operator = rig(tmp_path)
    slot = auth.admit("old-gateway", 1, "single")
    auth.bind("old-gateway", 1, slot, SERVER, GENERATION, TXID)
    body, signature = witness(auth, signer, slot)
    with pytest.raises(FenceDenied):
        auth.close(alter(body), signature)
    assert auth.snapshot()["pending"] == 1
    auth.close(body, signature)


def test_wrong_signer_wrong_term_and_replay_denied(tmp_path):
    auth, signer, _, _, operator = rig(tmp_path)
    slot = auth.admit("old-gateway", 1, "operation")
    auth.bind("old-gateway", 1, slot, SERVER, GENERATION, TXID)
    body, sig = witness(auth, signer, slot)
    with pytest.raises(FenceDenied, match="INVALID_WITNESS_SIGNATURE"):
        auth.close(body, Ed25519PrivateKey.generate().sign(_canon(body)))
    assert auth.snapshot()["pending"] == 1
    auth.close(body, sig)
    with pytest.raises(FenceDenied, match="WITNESS_ATTEMPT_MISMATCH"):
        auth.close(body, sig)
    assert auth.snapshot()["pending"] == 0


def test_rollback_of_database_alone_fails_closed(tmp_path):
    auth, _, db_file, anchor, operator = rig(tmp_path)
    old_copy = tmp_path / "old-state.sqlite"
    shutil.copy2(db_file, old_copy)
    auth.admit("old-gateway", 1, "current-work")
    shutil.copy2(old_copy, db_file)
    with pytest.raises(FenceDenied, match="FENCING_CHECKPOINT_MISMATCH"):
        ResearchFencingAuthority(db_file, anchor)
    with pytest.raises(FenceDenied, match="FENCING_CHECKPOINT_MISMATCH"):
        auth.admit("old-gateway", 1, "would-overadmit")


def test_missing_or_corrupt_checkpoint_never_falls_back(tmp_path):
    auth, _, _, anchor, operator = rig(tmp_path)
    anchor.unlink()
    with pytest.raises(FenceDenied, match="ANCHOR_UNAVAILABLE"):
        auth.admit("old-gateway", 1, "unsafe")
    anchor.write_text("{}")
    with pytest.raises(FenceDenied, match="ANCHOR_INVALID"):
        auth.snapshot()


def test_checkpoint_advances_before_sql_commit_if_sql_fails(tmp_path, monkeypatch):
    # An artificial mismatch models a crash between files; no auto-repair.
    auth, _, _, anchor, operator = rig(tmp_path)
    raw = json.loads(anchor.read_text())
    raw["revision"] += 1
    anchor.write_text(json.dumps(raw))
    with pytest.raises(FenceDenied, match="FENCING_CHECKPOINT_MISMATCH"):
        auth.admit("old-gateway", 1, "fail-closed")


def test_capacity_is_atomic_across_parallel_clients(tmp_path):
    auth, _, _, _, operator = rig(tmp_path, capacity=3)
    def one(i):
        try:
            return auth.admit("old-gateway", 1, f"parallel-{i}")
        except FenceDenied:
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(one, range(12)))
    assert len([x for x in results if x]) == 3
    assert auth.snapshot()["pending"] == 3
    with pytest.raises(FenceDenied, match="UNCERTAIN_INFLIGHT"):
        auth.takeover("next", 1, *approval(auth, operator, "next"))


def test_double_bootstrap_and_old_owner_takeover_denied(tmp_path):
    auth, signer, db, anchor, operator = rig(tmp_path)
    with pytest.raises(FenceDenied, match="BOOTSTRAP_REQUIRES_FRESH_STORAGE"):
        ResearchFencingAuthority.bootstrap(db, anchor, "rogue", 16, b"x" * 32,
                                           b"y" * 32)
    with pytest.raises(FenceDenied, match="SAME_OWNER"):
        auth.takeover("old-gateway", 1, *approval(auth, operator, "old-gateway"))
    with pytest.raises(FenceDenied, match="STALE_FENCING_TERM"):
        auth.takeover("new-gateway", 999, *approval(auth, operator, "new-gateway"))


def test_coordinated_copy_remains_negative_not_quorum_proof(tmp_path):
    auth, signer, db, anchor, operator = rig(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    clone_db, clone_anchor = clone / "authority.sqlite", clone / "checkpoint.json"
    shutil.copy2(db, clone_db)
    shutil.copy2(anchor, clone_anchor)
    independently_restored = ResearchFencingAuthority(clone_db, clone_anchor)
    # Both independent copies can admit; do NOT call that an enforcement PASS.
    first = auth.admit("old-gateway", 1, "same-op")
    second = independently_restored.admit("old-gateway", 1, "same-op")
    assert first != second
    assert auth.snapshot()["pending"] == 1
    assert independently_restored.snapshot()["pending"] == 1
    # This is the explicit reproduced multi-primary split-brain COUNTEREXAMPLE.

def test_no_takeover_without_valid_operator_signature(tmp_path):
    auth, signer, _, _, operator = rig(tmp_path)
    body, sig = approval(auth, operator, "successor")
    with pytest.raises(FenceDenied, match="INVALID_OPERATOR_APPROVAL"):
        auth.takeover("successor", 1, dict(body, next_owner="attacker"), sig)
    with pytest.raises(FenceDenied, match="INVALID_OPERATOR_SIGNATURE"):
        auth.takeover("successor", 1, body,
                      Ed25519PrivateKey.generate().sign(_canon(body)))
    assert auth.takeover("successor", 1, body, sig) == 2
    with pytest.raises(FenceDenied, match="STALE_FENCING_TERM"):
        auth.takeover("successor", 1, body, sig)
