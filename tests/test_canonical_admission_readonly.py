"""Synthetic graph responses only; tests never access production database."""
import importlib.util
import pathlib
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "admission_ro", ROOT / "scripts/inspect-canonical-admission-readonly.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class FakeSession:
    def __init__(self, state, counts=None):
        self.state = state
        self.counts = counts or {}
        self.queries = []

    def run(self, query, **params):
        self.queries.append((query, params))
        if "FleetProjectionState" in query:
            row = self.state
        else:
            key = next((label for label, _ in mod.LABELS if f"MATCH (n:{label})" in query), None)
            assert key is not None
            row = self.counts.get(key, {})
        return FakeResult(row)


class FakeResult:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row


def test_expired_generation_is_hold_and_has_no_dispatched_generation():
    now = 20000000
    sess = FakeSession(
        {"generation": 896, "status": "approved", "expires_at_ts": now - 2000,
         "approved_by": "operator", "approval_id": "change-123"},
        {label: {"total": 10, "canonical_generation": 1, "unexpired": 0,
                 "eligible_by_fields": 0} for label, _ in mod.LABELS},
    )
    report = mod.inspect(sess, now)
    assert report["gate"] == "HOLD"
    assert not report["can_dispatch"]
    assert report["canonical_expired_by_seconds"] == 2
    assert "canonical-approval-expired-or-absent" in report["reasons"]
    assert len(sess.queries) == 5
    assert all("MATCH" in query and all(x not in query.upper()
        for x in (" DELETE ", " SET ", " MERGE ", " CREATE ", " CALL ", " LOAD CSV "))
        for query, _ in sess.queries)
    assert report["record_counts"]["LoadedModelInstance"]["eligible_by_fields"] == 0


def test_fresh_counts_still_cannot_authorize_dispatch():
    now = 20000000
    state = {"generation": 897, "status": "approved", "expires_at_ts": now + 5000,
             "approved_by": "operator", "approval_id": "approved-id"}
    counts = {label: {"total": 1, "canonical_generation": 1,
                      "unexpired": 1, "eligible_by_fields": 1} for label, _ in mod.LABELS}
    report = mod.inspect(FakeSession(state, counts), now)
    assert report["canonical_approval_complete_and_fresh"]
    assert report["gate"] == "HOLD_READINESS_NOT_PROVEN"
    assert not report["can_dispatch"]
    assert not report["authorizes_approval"]
    assert report["model_requests_sent"] == 0


def test_missing_state_and_missing_records_fail_closed():
    report = mod.inspect(FakeSession(None), 20000000)
    assert report["canonical_generation"] is None
    assert report["gate"] == "HOLD"
    assert "canonical-state-missing" in report["reasons"]
    assert report["record_counts"]["AccessPath"]["total"] == 0


def test_incomplete_approval_identity_fails_closed():
    report = mod.inspect(
        FakeSession({"generation": 10, "status": "approved",
                    "expires_at_ts": 21000000, "approved_by": "",
                    "approval_id": "id"}), 20000000
    )
    assert report["gate"] == "HOLD"
    assert "canonical-approval-identity-or-status-invalid" in report["reasons"]


def test_sensitive_identifiers_not_reflected_in_report():
    report = mod.inspect(FakeSession({
        "generation": 100, "status": "approved", "expires_at_ts": 20000001,
        "approved_by": "private-operator", "approval_id": "secret-evidence"
    }), 20000000)
    assert "secret-evidence" not in str(report)
    assert "private-operator" not in str(report)
