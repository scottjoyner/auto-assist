"""Research-only cross-ledger uncertainty, rollback and bypass contracts.

The two SQLite files are on ONE HOST with ONE ADMIN. All reconciliations HOLD.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest

from dual_authority_lab import Authority, Decision, Resource, reconcile


@pytest.fixture
def separated(tmp_path):
    guard_path = tmp_path / "simulated_authority"
    res_path = tmp_path / "simulated_resource"
    guard_path.mkdir()
    res_path.mkdir()
    authority = Authority(guard_path / "spent.db")
    resource = Resource(res_path / "effect.db")
    authority.bootstrap()
    resource.bootstrap()
    return authority, resource


def digest(s: str = "initial") -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_empty_domains_undetermined(separated):
    a, r = separated
    x = reconcile(a, r, "op:empty", digest())
    assert x == Decision("UNISSUED_UNPROVEN")


def test_reservation_is_durable_without_effect_and_stays_hold(separated):
    a, r = separated
    assert a.reserve("op:prepared", digest()) == "PREPARED_NEW"
    assert a.reserve("op:prepared", digest()) == "PREPARED_EXISTS"
    x = reconcile(a, r, "op:prepared", digest())
    assert x.state == "PREPARED_UNCERTAIN"
    assert not x.may_retry_effect and not x.may_ack
    assert r.read("op:prepared") is None


def test_concurrent_reserve_is_single_owner(separated):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    a, r = separated
    b = Barrier(6)

    def try_reserve(_):
        b.wait(timeout=8)
        return a.reserve("op:concurrent", digest())

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(try_reserve, range(6)))
    assert statuses.count("PREPARED_NEW") == 1
    assert statuses.count("PREPARED_EXISTS") == 5
    assert reconcile(a, r, "op:concurrent", digest()).state == "PREPARED_UNCERTAIN"


def test_same_key_modified_payload_conflict(separated):
    a, r = separated
    assert a.reserve("op:conflict", digest()) == "PREPARED_NEW"
    assert a.reserve("op:conflict", digest("changed")) == "CONFLICT"
    assert reconcile(a, r, "op:conflict", digest("changed")).state == "CLAIM_DIGEST_CONFLICT"


def test_effect_created_before_authority_finalization_is_uncertain(separated):
    a, r = separated
    assert a.reserve("op:partial", digest()) == "PREPARED_NEW"
    assert r.apply_direct("op:partial", digest()) == "EFFECT_WRITTEN"
    x = reconcile(a, r, "op:partial", digest())
    assert x.state == "EFFECT_UNACKED"
    assert not x.may_ack and not x.may_retry_effect


def test_consistent_readback_still_not_an_attested_commit(separated):
    a, r = separated
    a.reserve("op:consistent", digest())
    r.apply_direct("op:consistent", digest())
    assert a.observe("op:consistent", digest(), r) == "OBSERVED_UNATTESTED"
    x = reconcile(a, r, "op:consistent", digest())
    assert x.state == "CONSISTENT_UNATTESTED"
    assert not x.may_ack and not x.may_promote


def test_resource_rollback_detected_when_guard_remains(separated, tmp_path):
    a, r = separated
    pristine = tmp_path / "effect-precommit.db"
    shutil.copy2(r.path, pristine)
    assert a.reserve("op:rollback", digest()) == "PREPARED_NEW"
    assert r.apply_direct("op:rollback", digest()) == "EFFECT_WRITTEN"
    assert a.observe("op:rollback", digest(), r) == "OBSERVED_UNATTESTED"
    assert reconcile(a, r, "op:rollback", digest()).state == "CONSISTENT_UNATTESTED"
    # Research-only privileged resource restore; authority file is NOT rolled back.
    shutil.copy2(pristine, r.path)
    decision = reconcile(a, r, "op:rollback", digest())
    assert decision.state == "RESOURCE_ROLLBACK_SUSPECTED"
    assert not decision.may_ack and not decision.may_retry_effect
    assert a.reserve("op:rollback", digest()) == "OBSERVED_EXISTS"
    assert r.read("op:rollback") is None


def test_full_dual_file_restore_reenables_old_operation_negative(separated, tmp_path):
    a, r = separated
    auth_pristine = tmp_path / "authority-precommit.db"
    resource_pristine = tmp_path / "resource-precommit.db"
    shutil.copy2(a.path, auth_pristine)
    shutil.copy2(r.path, resource_pristine)
    assert a.reserve("op:dualrestore", digest()) == "PREPARED_NEW"
    assert r.apply_direct("op:dualrestore", digest()) == "EFFECT_WRITTEN"
    assert a.observe("op:dualrestore", digest(), r) == "OBSERVED_UNATTESTED"
    assert reconcile(a, r, "op:dualrestore", digest()).state == "CONSISTENT_UNATTESTED"
    shutil.copy2(auth_pristine, a.path)
    shutil.copy2(resource_pristine, r.path)
    assert reconcile(a, r, "op:dualrestore", digest()).state == "UNISSUED_UNPROVEN"
    assert a.reserve("op:dualrestore", digest()) == "PREPARED_NEW"
    assert r.apply_direct("op:dualrestore", digest()) == "EFFECT_WRITTEN"
    # Same operator restored BOTH files: second logical effect, only one visible.


def test_direct_resource_owner_bypass_without_guard(separated):
    a, r = separated
    assert r.apply_direct("op:unauthorized", digest()) == "EFFECT_WRITTEN"
    assert reconcile(a, r, "op:unauthorized", digest()).state == "ORPHAN_EFFECT"
    assert a.read("op:unauthorized") is None


def test_guard_only_rollback_exposes_orphan_effect(separated, tmp_path):
    a, r = separated
    backup = tmp_path / "guard-precommit.db"
    shutil.copy2(a.path, backup)
    a.reserve("op:guardrewind", digest())
    r.apply_direct("op:guardrewind", digest())
    a.observe("op:guardrewind", digest(), r)
    shutil.copy2(backup, a.path)
    assert reconcile(a, r, "op:guardrewind", digest()).state == "ORPHAN_EFFECT"


def test_absent_authority_readback_does_not_create_ledger(separated):
    a, r = separated
    a.path.unlink()
    assert reconcile(a, r, "op:missing-guard", digest()).state == "AUTHORITY_UNAVAILABLE"
    assert not a.path.exists()


def test_absent_resource_readback_does_not_create_effect_db(separated):
    a, r = separated
    r.path.unlink()
    assert reconcile(a, r, "op:missing-effect", digest()).state == "RESOURCE_UNAVAILABLE"
    assert not r.path.exists()


def test_mismatched_resource_effect_never_authorizes_ack(separated):
    a, r = separated
    a.reserve("op:diff", digest())
    r.apply_direct("op:diff", digest("other"))
    assert reconcile(a, r, "op:diff", digest()).state == "RESOURCE_DIGEST_CONFLICT"
    assert a.observe("op:diff", digest(), r) == "RESOURCE_NOT_VERIFIED"


def test_missing_guard_cannot_be_observed(separated):
    a, r = separated
    r.apply_direct("op:orphan", digest())
    assert a.observe("op:orphan", digest(), r) == "CONFLICT_OR_MISSING"


def test_invalid_input_denied_without_writes(separated):
    a, r = separated
    assert a.reserve("", digest()) == "INVALID"
    assert r.apply_direct("", digest()) == "INVALID"
    assert reconcile(a, r, "?", digest()).state == "BAD_OPERATION_IDENTITY"
    assert reconcile(a, r, "op:valid", "wrong").state == "BAD_OPERATION_IDENTITY"
    assert reconcile(a, r, "op:valid", digest(), term=True).state == "BAD_TERM"
    assert a.read("op:valid") is None


def test_unknown_term_fenced_and_no_old_ack_after_term_change(separated):
    a, r = separated
    a.reserve("op:term", digest())
    r.apply_direct("op:term", digest())
    a.observe("op:term", digest(), r)
    with sqlite3.connect(a.path) as db:
        db.execute("UPDATE term_head SET term=2 WHERE scope='context'")
    assert a.reserve("op:term", digest(), term=1) == "FENCED"
    assert reconcile(a, r, "op:term", digest(), term=1).state == "TERM_FENCED"


def test_simulated_lost_ack_never_auto_retries(separated):
    a, r = separated
    a.reserve("op:lost-ack", digest())
    r.apply_direct("op:lost-ack", digest())
    # Simulate an exception or unavailable guard before observe():
    prior = reconcile(a, r, "op:lost-ack", digest())
    assert prior.state == "EFFECT_UNACKED"
    assert not prior.may_ack and not prior.may_retry_effect
    # Separate read-only reconciliation also remains fail-closed.
    subsequent = reconcile(a, r, "op:lost-ack", digest())
    assert subsequent == prior


@pytest.mark.parametrize("state", [
    "UNISSUED_UNPROVEN", "PREPARED_UNCERTAIN", "EFFECT_UNACKED",
    "CONSISTENT_UNATTESTED", "RESOURCE_ROLLBACK_SUSPECTED",
    "ORPHAN_EFFECT", "AUTHORITY_UNAVAILABLE", "RESOURCE_UNAVAILABLE",
    "CLAIM_DIGEST_CONFLICT", "RESOURCE_DIGEST_CONFLICT", "TERM_FENCED",
])
def test_any_observed_state_has_no_ack_or_automatic_retry(state):
    d = Decision(state)
    assert d.decision == "HOLD"
    assert not d.may_ack and not d.may_retry_effect and not d.may_promote



def test_readback_to_observed_effect_time_toctou_negative(separated):
    """Resource can change AFTER sampled readback but BEFORE guard OBSERVED."""
    a, r = separated
    a.reserve("op:read-race", digest())
    r.apply_direct("op:read-race", digest())
    original_read = r.read

    def stale_resource_read(key):
        prior = original_read(key)
        with sqlite3.connect(r.path) as db:
            db.execute("DELETE FROM effects WHERE op_key=?", (key,))
        return prior

    # No graph or daemon: monkeypatched disposable object simulates effect-time race.
    r.read = stale_resource_read
    assert a.observe("op:read-race", digest(), r) == "OBSERVED_UNATTESTED"
    assert a.read("op:read-race") == (digest(), "OBSERVED")
    r.read = original_read
    observed = reconcile(a, r, "op:read-race", digest())
    assert observed.state == "RESOURCE_ROLLBACK_SUSPECTED"
    assert not observed.may_ack


def test_guard_observe_after_term_revocation_is_unsafe_state_transition(separated):
    """Observe lacks resource-side signed current-term check; release HOLD."""
    a, r = separated
    a.reserve("op:stale-observe", digest(), term=1)
    r.apply_direct("op:stale-observe", digest())
    with sqlite3.connect(a.path) as db:
        db.execute("UPDATE term_head SET term=2 WHERE scope='context'")
    # Source-level negative: naive observe accepts a matching but OLD claim.
    assert a.observe("op:stale-observe", digest(), r) == "OBSERVED_UNATTESTED"
    assert reconcile(a, r, "op:stale-observe", digest(), term=1).state == "TERM_FENCED"
    assert reconcile(a, r, "op:stale-observe", digest(), term=2).state == "CONSISTENT_UNATTESTED"
    # No ACK even in the apparently consistent current-term view.
    assert not reconcile(a, r, "op:stale-observe", digest(), term=2).may_ack
