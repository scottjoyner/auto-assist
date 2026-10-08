"""Deny-only offline Mercury specialist adapter acceptance. No provider requests."""
from dataclasses import replace
from pathlib import Path
import importlib.util
import json
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/mercury_shadow_specialists.py"
spec = importlib.util.spec_from_file_location("mercury_shadow_specialists", SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class Gate:
    def __init__(self):
        self.calls = []
        self.deny = False
        self.renew_denied = False
        self.wrong_group = False
        self.expired = False
        self.release_fails = False

    def acquire(self, task, policy):
        self.calls.append("acquire")
        if self.deny:
            return None
        return m.Lease("synthetic-lease-one", "wrong" if self.wrong_group else task.group,
                       0 if self.expired else 20000)

    def renew(self, lease, task):
        self.calls.append("renew")
        if self.renew_denied:
            return None
        return lease

    def release(self, lease, task):
        self.calls.append("release")
        return not self.release_fails

    def trip(self, task, status):
        self.calls.append("trip:" + str(status))


Worker = m.FixtureWorker


@pytest.fixture
def task():
    return m.Task("mercury-demo-0001", "repository-reviewer", m.objective_digest("read-only task"))


@pytest.fixture
def journal(tmp_path):
    return m.Journal(tmp_path / "custody" / "events.jsonl")


def test_disabled_denies_before_any_lease_or_execution(task, journal):
    gate, worker = Gate(), Worker()
    outcome = m.MercuryShadowAdapter(journal, gate).execute(task, worker)
    assert (outcome.status, outcome.reason) == ("denied", "shadow_disabled")
    assert gate.calls == []
    assert worker.started == 0
    assert [e["event"] for e in journal.records()] == ["submitted", "terminal"]


def test_missing_authority_denies(task, journal):
    worker = Worker()
    outcome = m.MercuryShadowAdapter(journal, enabled=True).execute(task, worker)
    assert outcome.reason == "admission_unavailable"
    assert worker.started == 0


@pytest.mark.parametrize("change", [
    {"role": "unbounded-admin"},
    {"model": "paid/anything"},
    {"provider": "openrouter"},
    {"group": "unverified-alias"},
    {"objective_sha256": "not-a-digest"},
    {"task_id": "../unsafe"},
])
def test_unknown_authority_or_unsafe_task_fails_before_any_io(task, journal, change):
    worker = Worker()
    with pytest.raises(ValueError):
        m.MercuryShadowAdapter(journal, Gate(), enabled=True).execute(
            replace(task, **change), worker)
    assert worker.started == 0
    assert journal.records() == []


@pytest.mark.parametrize("mode", ["deny", "wrong_group", "expired"])
def test_bad_lease_denies_before_worker(task, journal, mode):
    gate, worker = Gate(), Worker()
    setattr(gate, mode, True)
    outcome = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert outcome.reason == "lease_denied"
    assert worker.started == 0


def test_valid_mock_specialist_has_custody_and_bounded_usage(task, journal):
    gate, worker = Gate(), Worker()
    result = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert (result.status, result.input_tokens, result.output_tokens, result.steps) == ("completed", 10, 5, 1)
    assert gate.calls == ["acquire", "renew", "renew", "release"]
    assert worker.cancelled == 0
    assert [e["event"] for e in journal.records()] == ["submitted", "admitted", "usage", "terminal"]
    assert journal.path.stat().st_mode & 0o077 == 0
    assert "read-only task" not in journal.path.read_text()


def test_duplicate_idempotency_never_reruns(task, journal):
    gate = Gate()
    adapter = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000)
    assert adapter.execute(task, Worker()).status == "completed"
    repeat = Worker()
    outcome = adapter.execute(task, repeat)
    assert outcome.reason == "duplicate_task_id"
    assert repeat.started == 0
    assert gate.calls.count("acquire") == 1


def test_renewal_failure_cancels_without_retry(task, journal):
    gate, worker = Gate(), Worker()
    gate.renew_denied = True
    outcome = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert outcome.reason == "renewal_denied"
    assert gate.calls.count("renew") == 1
    assert gate.calls.count("acquire") == 1
    assert worker.cancelled == 1


@pytest.mark.parametrize("status", [401, 402, 403, 429, 503])
def test_provider_trip_aborts_without_retry(task, journal, status):
    gate = Gate()
    worker = Worker(m.Event("provider_error", error_status=status), m.Event("finish"))
    outcome = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert outcome.status == "cancelled"
    assert gate.calls.count("trip:" + str(status)) == 1
    assert gate.calls.count("acquire") == 1
    assert worker.cancelled == 1


@pytest.mark.parametrize("event, reason", [
    (m.Event("usage", input_tokens=6001), "budget_exceeded"),
    (m.Event("usage", output_tokens=801), "budget_exceeded"),
    (m.Event("usage", input_tokens=-1), "invalid_token_event"),
    (m.Event("usage", input_tokens=True), "invalid_token_event"),
    (m.Event("usage", model="paid/unknown"), "event_model_mismatch"),
    (m.Event("tool_start"), "unknown_event"),
])
def test_budget_and_event_mismatch_fail_closed(task, journal, event, reason):
    gate, worker = Gate(), Worker(event, m.Event("finish"))
    outcome = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert (outcome.status, outcome.reason) == ("cancelled", reason)
    assert worker.cancelled == 1


def test_step_cap_and_missing_finish_do_not_claim_success(task, journal):
    events = tuple(m.Event("usage", input_tokens=1) for _ in range(4))
    outcome = m.MercuryShadowAdapter(journal, Gate(), enabled=True, clock=lambda: 10000).execute(
        task, Worker(*events))
    assert outcome.reason == "budget_exceeded"


def test_release_failure_cannot_claim_completed(task, journal):
    gate = Gate()
    gate.release_fails = True
    outcome = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, Worker())
    assert (outcome.status, outcome.reason) == ("cancelled", "release_unverified")


def test_chain_tampering_is_detected_and_blocks_future_dispatch(task, journal):
    gate = Gate()
    adapter = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000)
    assert adapter.execute(task, Worker()).status == "completed"
    rows = journal.path.read_text().replace('"input_tokens":10', '"input_tokens":999')
    journal.path.write_text(rows)
    with pytest.raises(m.CustodyError, match="trace_chain_invalid"):
        journal.records()
    next_worker = Worker()
    with pytest.raises(m.CustodyError):
        adapter.execute(replace(task, task_id="mercury-demo-0002"), next_worker)
    assert next_worker.started == 0


def test_untrusted_journal_mode_blocks_execution(task, journal):
    journal.path.write_text("")
    journal.path.chmod(0o644)
    with pytest.raises(m.CustodyError, match="untrusted_journal"):
        m.MercuryShadowAdapter(journal, Gate(), enabled=True).execute(task, Worker())


def test_failed_admitted_write_releases_lease_without_running_worker(task, journal):
    gate, worker = Gate(), Worker()
    actual = journal.append

    def broken(t, kind, **fields):
        if kind == "admitted":
            raise m.CustodyError("disk_full")
        return actual(t, kind, **fields)

    journal.append = broken
    with pytest.raises(m.CustodyError):
        m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert worker.started == 0
    assert "release" in gate.calls


def test_no_prompt_or_tool_output_fields_can_enter_journal(task, journal):
    with pytest.raises(m.CustodyError, match="unapproved_trace_field"):
        journal.append(task, "usage", prompt="secret string")
    assert journal.records() == []


def test_all_specialist_roles_have_finite_budgets():
    assert len(m.SPECIALISTS) == 3
    for role, policy in m.SPECIALISTS.items():
        assert policy.role == role
        assert 0 < policy.input_cap <= 6000
        assert 0 < policy.output_cap <= 900
        assert 0 < policy.step_cap <= 3


def test_revocation_is_checked_before_first_fixture_event(task, journal):
    gate = Gate()
    gate.renew_denied = True
    worker = Worker(m.Event("usage", input_tokens=100), m.Event("finish"))
    result = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert result.reason == "renewal_denied"
    assert worker.started == 0
    assert worker.cancelled == 1
    assert not [r for r in journal.records() if r["event"] == "usage"]


def test_foreign_worker_is_not_accepted_even_in_explicit_simulation(task, journal):
    class UnsafeWorker:
        def events(self, task):
            raise AssertionError("this must never execute")
        def cancel(self):
            pass

    gate = Gate()
    with pytest.raises(ValueError, match="shadow_requires_exact_fixture_worker"):
        m.MercuryShadowAdapter(journal, gate, enabled=True).execute(task, UnsafeWorker())
    assert gate.calls == [] and journal.records() == []


def test_no_finished_event_is_not_accepted(task, journal):
    gate = Gate()
    worker = Worker(m.Event("usage", input_tokens=20, output_tokens=2))
    outcome = m.MercuryShadowAdapter(journal, gate, enabled=True, clock=lambda: 10000).execute(task, worker)
    assert (outcome.status, outcome.reason) == ("cancelled", "missing_finish")
    assert worker.cancelled == 1


@pytest.mark.parametrize("role", sorted(m.SPECIALISTS))
def test_each_specialist_role_can_complete_a_bounded_fixture(role, task, tmp_path):
    journal = m.Journal(tmp_path / role / "events.jsonl")
    specialized = replace(task, task_id="assignment-" + role, role=role)
    outcome = m.MercuryShadowAdapter(journal, Gate(), enabled=True, clock=lambda: 10000).execute(
        specialized, Worker(m.Event("usage", input_tokens=50, output_tokens=10), m.Event("finish")))
    assert outcome.status == "completed"
    assert journal.records()[-1]["role"] == role


def test_journal_rejects_symlink_target(task, tmp_path):
    target = tmp_path / "target.jsonl"
    target.write_text("protected")
    symlink = tmp_path / "journal.jsonl"
    symlink.symlink_to(target)
    with pytest.raises(m.CustodyError, match="unsafe_journal_path"):
        m.Journal(symlink)
    assert target.read_text() == "protected"


def test_journal_allows_zero_sized_clean_empty_history(tmp_path):
    journal = m.Journal(tmp_path / "shadow.jsonl")
    assert journal.records() == []