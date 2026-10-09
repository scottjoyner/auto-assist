"""Mock-only safety gates. Zero real provider calls and no Mercury BotManager."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import importlib.util
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/mercury_shadow_specialists.py"
spec = importlib.util.spec_from_file_location("mercury_mock_acceptance", SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def task(idx=1):
    return m.Task(f"mock-acceptance-{idx:04d}", "repository-reviewer",
                  m.objective_digest(f"read-only fixture {idx}"))


def authority(tmp_path, *, clock=None, input_capacity=6000, output_capacity=900):
    return m.MockSharedAuthority(tmp_path / "authority" / "leases.sqlite",
                                 clock=clock or (lambda: 1000.0),
                                 input_capacity=input_capacity,
                                 output_capacity=output_capacity)


def get_lease(gate, t):
    lease = gate.acquire(t, m.SPECIALISTS[t.role])
    assert lease is not None
    return lease


def test_eight_concurrent_contenders_share_one_slot(tmp_path):
    gate_file = tmp_path / "atomic" / "lease.sqlite"

    def contender(index):
        gate = m.MockSharedAuthority(gate_file, clock=lambda: 1000.0)
        return gate.acquire(task(index), m.SPECIALISTS["repository-reviewer"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(contender, range(1, 9)))
    admitted = [lease for lease in leases if lease]
    assert len(admitted) == 1
    assert len({lease.task_id for lease in admitted}) == 1


def test_idempotency_remains_denied_across_coordinator_restart(tmp_path):
    t = task()
    gate = authority(tmp_path, input_capacity=18000, output_capacity=2700)
    lease = get_lease(gate, t)
    assert gate.release(lease, t)
    restarted = authority(tmp_path, input_capacity=18000, output_capacity=2700)
    assert restarted.acquire(t, m.SPECIALISTS[t.role]) is None
    assert restarted.acquire(replace(t, attempt_id="attempt-0002"),
                             m.SPECIALISTS[t.role]) is None
    assert restarted.acquire(task(2), m.SPECIALISTS[t.role]) is not None


def test_pre_reserved_aggregate_quota_not_refunded_by_release(tmp_path):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    assert gate.release(lease, t)
    assert gate.acquire(task(2), m.SPECIALISTS[t.role]) is None


@pytest.mark.parametrize("input_capacity,output_capacity", [(5999, 900), (6000, 799)])
def test_insufficient_upstream_reservation_denies_before_worker(tmp_path, input_capacity, output_capacity):
    gate = authority(tmp_path, input_capacity=input_capacity, output_capacity=output_capacity)
    worker = m.FixtureWorker()
    journal = m.Journal(tmp_path / "events.jsonl")
    result = m.MercuryShadowAdapter(journal, gate, enabled=True,
                                    clock=lambda: 1000.0).execute(task(), worker)
    assert result.status == "denied"
    assert worker.started == 0


def test_fresh_group_expiration_allows_new_task_but_not_stale_renewal(tmp_path):
    now = [1000.0]
    gate = authority(tmp_path, clock=lambda: now[0], input_capacity=12000,
                     output_capacity=1800)
    a = task()
    old = get_lease(gate, a)
    now[0] = old.expires_at + 1.0
    assert gate.renew(old, a) is None
    assert gate.acquire(task(2), m.SPECIALISTS[a.role]) is not None


@pytest.mark.parametrize("mutation", [
    {"node_id": "foreign-node-2"},
    {"model": "paid/not-authorized"},
    {"group": "alias-new-group"},
    {"role": "trace-auditor"},
    {"attempt_id": "attempt-0002"},
    {"authority_epoch": 2},
    {"task_id": "mock-acceptance-9999"},
])
def test_wrong_identity_and_supersession_denied_at_fake_call_site(tmp_path, mutation):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    journal = m.Journal(tmp_path / "receipts.jsonl")
    site = m.FakeProviderCallSite(gate, journal, m.MockCustodyWitness())
    changed = replace(t, **mutation)
    if mutation.get("model") or mutation.get("group"):
        with pytest.raises(ValueError):
            changed.validate()
    else:
        assert not site.request(task=changed, lease=lease, ingress="router")
    assert site.synthetic_call_count == 0


@pytest.mark.parametrize("ingress", [
    "webhook", "cron", "message", "run", "peer", "dlq_replay", "mailbox", "autocrew"
])
def test_untrusted_ingress_never_reaches_fake_call_site(tmp_path, ingress):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    site = m.FakeProviderCallSite(
        gate, m.Journal(tmp_path / "trace.jsonl"), m.MockCustodyWitness())
    assert site.request(task=t, lease=lease, ingress=ingress) is False
    assert site.synthetic_call_count == 0


def test_revocation_denies_followup_fake_call(tmp_path):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    gate.revoke(lease.lease_id)
    site = m.FakeProviderCallSite(
        gate, m.Journal(tmp_path / "trace.jsonl"), m.MockCustodyWitness())
    assert gate.renew(lease, t) is None
    assert site.request(task=t, lease=lease, ingress="router") is False
    assert site.synthetic_call_count == 0


def test_expired_lease_denies_fake_provider_call(tmp_path):
    now = [1000.0]
    gate = authority(tmp_path, clock=lambda: now[0])
    t = task()
    lease = get_lease(gate, t)
    now[0] = lease.expires_at + 1.0
    site = m.FakeProviderCallSite(
        gate, m.Journal(tmp_path / "trace.jsonl"), m.MockCustodyWitness())
    assert site.request(task=t, lease=lease, ingress="router") is False
    assert site.synthetic_call_count == 0


def test_failed_admission_ack_denies_work_and_releases_lease(tmp_path):
    gate = authority(tmp_path)
    t = task()
    worker = m.FixtureWorker()
    witness = m.MockCustodyWitness(fail_on=frozenset({"admitted"}))
    adapter = m.MercuryShadowAdapter(
        m.Journal(tmp_path / "trace.jsonl"), gate, enabled=True,
        clock=lambda: 1000.0, witness=witness)
    with pytest.raises(m.CustodyError, match="mock_witness_denied"):
        adapter.execute(t, worker)
    assert worker.started == 0
    # Duplicate task remains claimed; no retry can bypass the failed receipt.
    assert gate.acquire(t, m.SPECIALISTS[t.role]) is None


def test_failed_fake_provider_call_ack_denies_counter(tmp_path):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    witness = m.MockCustodyWitness(fail_on=frozenset({"provider_call_fixture"}))
    site = m.FakeProviderCallSite(
        gate, m.Journal(tmp_path / "trace.jsonl"), witness)
    assert site.request(task=t, lease=lease, ingress="router") is False
    assert site.synthetic_call_count == 0


def test_authorized_synthetic_request_is_typed_and_receipted(tmp_path):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    journal = m.Journal(tmp_path / "trace.jsonl")
    witness = m.MockCustodyWitness()
    site = m.FakeProviderCallSite(gate, journal, witness)
    assert site.request(task=t, lease=lease, ingress="router") is True
    assert site.synthetic_call_count == 1
    record = journal.records()[0]
    assert record["event"] == "provider_call_fixture"
    assert record["lease_id"] == lease.lease_id
    assert (record["node_id"], record["attempt_id"], record["authority_epoch"]) == (
        t.node_id, t.attempt_id, t.authority_epoch)
    assert witness.receipts == [record["event_hash"]]


def test_tampered_lease_reservation_cannot_bypass_call_site(tmp_path):
    gate = authority(tmp_path)
    t = task()
    lease = get_lease(gate, t)
    site = m.FakeProviderCallSite(
        gate, m.Journal(tmp_path / "trace.jsonl"), m.MockCustodyWitness())
    assert not site.request(task=t, lease=replace(lease, reserved_input=1),
                            ingress="router")
    assert site.synthetic_call_count == 0


def test_mock_authority_rejects_cheaper_policy_for_same_role(tmp_path):
    gate = authority(tmp_path)
    fake = m.Specialist("repository-reviewer", 1, 1, 1, "forged allowance")
    with pytest.raises(ValueError, match="mock_policy_mismatch"):
        gate.acquire(task(), fake)
    assert get_lease(gate, task()) is not None


def test_parallel_adapter_submissions_never_start_two_workers(tmp_path):
    shared = tmp_path / "authority.sqlite"
    trace = tmp_path / "trace.jsonl"

    def attempt(_):
        gate = m.MockSharedAuthority(shared, clock=lambda: 1000.0)
        worker = m.FixtureWorker()
        outcome = m.MercuryShadowAdapter(
            m.Journal(trace), gate, enabled=True, clock=lambda: 1000.0
        ).execute(task(1), worker)
        return (outcome.status, worker.started)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(started for _, started in results) == 1
    assert sum(status == "completed" for status, _ in results) == 1
    assert sum(status == "denied" for status, _ in results) == 1
