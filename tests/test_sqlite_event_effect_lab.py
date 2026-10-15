"""Research-only SQLite transactional effect proof and deliberate NO-GO controls.

Tests use pytest.tmp_path, never Neo4j, credentials, production graph or network.
"""
from __future__ import annotations

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from sqlite_event_effect_lab import (
    SyntheticAckLost,
    SyntheticEffectStore,
    SyntheticInterruption,
    effect_digest,
)


@pytest.fixture
def store(tmp_path):
    lab = SyntheticEffectStore(tmp_path / "disposable.sqlite3")
    lab.bootstrap()
    return lab


def digest(payload=None, actor="test-principal"):
    return effect_digest(
        actor=actor, action="assign", target="test-task-1",
        payload=payload if payload is not None else {"agent": "isolated-agent"},
    )


def test_semantic_hash_excludes_transport_fields_but_binds_actor_payload():
    a = digest()
    assert len(a) == 64
    assert digest({"agent": "isolated-agent"}) == a
    assert digest({"agent": "other"}) != a
    assert digest(actor="other-principal") != a
    assert effect_digest(actor="test-principal", action="assign", target="test-task-1",
                         payload={"a": 1, "b": 2}) == effect_digest(
        actor="test-principal", action="assign", target="test-task-1",
        payload={"b": 2, "a": 1},
    )


@pytest.mark.parametrize("payload", [float("nan"), float("inf"), object()])
def test_non_json_semantic_payload_denied(payload):
    with pytest.raises(ValueError):
        digest({"unsafe": payload})


@pytest.mark.parametrize("key", ["", " ", "!", "a" * 129, None, True])
def test_invalid_keys_denied(store, key):
    assert store.apply(key=key, digest=digest()).status == "INVALID_KEY"
    assert store.readback()["revision"] == 0


def test_initial_commit_single_receipt_and_explicit_duplicate_ack(store):
    first = store.apply(key="op:1", digest=digest(), expected_revision=0)
    assert first.status == "COMMITTED" and first.may_ack and not first.may_retry
    after = store.readback()
    assert len(after["ops"]) == 1 == len(after["effects"])
    assert after["revision"] == 1
    duplicate = store.apply(key="op:1", digest=digest(), expected_revision=0)
    assert duplicate.status == "DUPLICATE_COMMITTED"
    assert duplicate.may_ack and not duplicate.may_retry
    assert duplicate.effect_id == first.effect_id
    assert store.readback() == after


def test_identical_key_different_semantics_does_not_alias(store):
    store.apply(key="op:same", digest=digest())
    other = store.apply(key="op:same", digest=digest({"agent": "changed"}))
    assert other.status == "CONFLICT" and not other.may_ack
    assert len(store.readback()["effects"]) == 1


@pytest.mark.parametrize("point", ["after_reserve", "after_effect"])
def test_precommit_fault_rolls_back_both_operation_and_effect(store, point):
    with pytest.raises(SyntheticInterruption):
        store.apply(key="op:fault", digest=digest(), fault=point)
    witness = store.readback()
    assert not witness["ops"] and not witness["effects"]
    assert witness["revision"] == 0
    after = store.apply(key="op:fault", digest=digest())
    assert after.status == "COMMITTED" and len(store.readback()["effects"]) == 1


def test_postcommit_ack_loss_is_deduped_after_new_connection(store):
    with pytest.raises(SyntheticAckLost):
        store.apply(key="op:acklost", digest=digest(), fault="after_commit_ack_lost")
    from_new_connection = SyntheticEffectStore(store.path)
    outcome = from_new_connection.apply(key="op:acklost", digest=digest())
    assert outcome.status == "DUPLICATE_COMMITTED" and outcome.may_ack
    assert len(from_new_connection.readback()["effects"]) == 1
    assert from_new_connection.readback()["revision"] == 1


def test_concurrent_same_key_only_one_effect(store):
    barrier = Barrier(8)

    def write(_):
        barrier.wait(timeout=8)
        return store.apply(key="op:concurrent", digest=digest()).status

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(write, range(8)))
    assert statuses.count("COMMITTED") == 1
    assert statuses.count("DUPLICATE_COMMITTED") == 7
    assert len(store.readback()["effects"]) == 1
    assert store.readback()["revision"] == 1


def test_concurrent_same_key_conflicting_payloads_only_one_can_commit(store):
    barrier = Barrier(2)

    def write(payload):
        barrier.wait(timeout=8)
        return store.apply(key="op:conflict", digest=digest(payload)).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(write, [{"a": 1}, {"a": 2}]))
    assert sorted([a, b]) == ["COMMITTED", "CONFLICT"]
    assert len(store.readback()["effects"]) == 1


def test_distinct_keys_with_stale_expected_revision_are_fenced(store):
    first = store.apply(key="op:one", digest=digest(), expected_revision=0)
    second = store.apply(key="op:two", digest=digest(), expected_revision=0)
    assert first.status == "COMMITTED"
    assert second.status == "STALE_REVISION_HOLD" and not second.may_ack
    assert len(store.readback()["effects"]) == 1


def test_old_term_cannot_ack_or_issue_after_admin_changes_term(store):
    store.apply(key="op:revoked", digest=digest())
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE fenced_scope SET term=2 WHERE scope='context'")
    assert store.apply(key="op:revoked", digest=digest(), term=1).status == "FENCED_HOLD"
    assert store.apply(key="op:new", digest=digest(), term=1).status == "FENCED_HOLD"
    assert not store.apply(key="op:revoked", digest=digest(), term=1).may_ack


def test_corrupt_commit_evidence_is_hold_not_success(store):
    store.apply(key="op:effect", digest=digest())
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM effects WHERE op_key='op:effect'")
    result = store.apply(key="op:effect", digest=digest())
    assert result.status == "UNCERTAIN_HOLD" and not result.may_ack


def test_disposable_privileged_restore_replays_spent_operation_negative(tmp_path):
    path = tmp_path / "restorable.sqlite3"
    store = SyntheticEffectStore(path)
    store.bootstrap()
    backup = tmp_path / "pre-commit-backup.sqlite3"
    shutil.copy2(path, backup)
    first = store.apply(key="op:rollback", digest=digest())
    assert first.status == "COMMITTED" and store.readback()["revision"] == 1
    # Deliberate operator restore in disposable temp path: spent state erased.
    shutil.copy2(backup, path)
    replay = SyntheticEffectStore(path).apply(key="op:rollback", digest=digest())
    assert replay.status == "COMMITTED"  # P0 antirollback FAIL by design
    assert SyntheticEffectStore(path).readback()["revision"] == 1
    # Two logical commits occurred, but only one remains visible after restore.


def _separate_process_apply(path: str, key: str, payload_hash: str) -> str:
    # Each worker opens a new SQLite connection inside a distinct OS process.
    return SyntheticEffectStore(__import__("pathlib").Path(path)).apply(
        key=key, digest=payload_hash,
    ).status


def test_distinct_os_processes_one_atomic_effect(store):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(_separate_process_apply, str(store.path),
                               "op:process-race", digest()) for _ in range(4)]
        statuses = [f.result(timeout=25) for f in futures]
    assert statuses.count("COMMITTED") == 1
    assert statuses.count("DUPLICATE_COMMITTED") == 3
    witness = store.readback()
    assert len(witness["ops"]) == len(witness["effects"]) == 1
    assert witness["revision"] == 1


def test_distinct_os_processes_two_effects_same_key_conflict(store):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(_separate_process_apply, str(store.path),
                               "op:process-conflict", digest({"actor": x}))
                   for x in ("actor-A", "actor-B")]
        statuses = [f.result(timeout=25) for f in futures]
    assert sorted(statuses) == ["COMMITTED", "CONFLICT"]
    assert len(store.readback()["effects"]) == 1