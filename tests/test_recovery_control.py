import pytest

from assistx.recovery_control import RecoveryControlPlane, recovery_fingerprint


class Store:
    def __init__(self):
        self.value = None

    def create(self, plan, actor, fingerprint):
        self.value = {
            "id": "recovery-1", "plan": plan, "fingerprint": fingerprint,
            "status": "PROPOSED", "expires_at_ts": 9999999999,
        }
        return dict(self.value)

    def get(self, _):
        return dict(self.value) if self.value else None

    def transition(self, _, expected, status, actor, result=None):
        if not self.value or self.value["status"] != expected:
            return None
        self.value.update(status=status, last_actor=actor, result=result or {})
        return dict(self.value)


def diagnosis():
    return {
        "diagnosis_id": "diag-1",
        "incident_key": "incident-1",
        "node_id": "node-a",
        "recommended_recovery": {
            "action": "restore_service",
            "risk": "critical",
            "verify_after": ["service_online"],
            "rollback": "restore_previous_control_state",
        },
    }


def test_recovery_requires_matching_fingerprint(monkeypatch):
    monkeypatch.delenv("ASSISTX_RECOVERY_EXECUTION_ENABLED", raising=False)
    control, store = RecoveryControlPlane(), Store()
    proposal = control.propose(store, diagnosis(), "operator")

    with pytest.raises(ValueError, match="fingerprint"):
        control.approve(store, proposal["id"], "wrong", "operator")

    approved = control.approve(store, proposal["id"], proposal["fingerprint"], "operator")
    assert approved["status"] == "APPROVED"


def test_recovery_execution_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSISTX_RECOVERY_EXECUTION_ENABLED", raising=False)
    control, store = RecoveryControlPlane(), Store()
    proposal = control.propose(store, diagnosis(), "operator")
    control.approve(store, proposal["id"], recovery_fingerprint(proposal["plan"]), "operator")

    result = control.execute(store, proposal["id"], "operator", lambda _: {"task_id": "unsafe"})

    assert result == {"executed": False, "blocked": True, "reason": "recovery_execution_disabled"}


def test_enabled_recovery_dispatches_guarded_runbook(monkeypatch):
    monkeypatch.setenv("ASSISTX_RECOVERY_EXECUTION_ENABLED", "true")
    control, store = RecoveryControlPlane(), Store()
    proposal = control.propose(store, diagnosis(), "operator")
    control.approve(store, proposal["id"], proposal["fingerprint"], "operator")

    result = control.execute(store, proposal["id"], "operator", lambda _: {"task_id": "task-1"})

    assert result["executed"] is True
    assert result["proposal"]["status"] == "DISPATCHED"

    outcome = control.record_outcome(
        store,
        proposal["id"],
        "verifier",
        verified=True,
        evidence={"service_online": True, "report_fresh": True},
    )
    assert outcome["status"] == "VERIFIED"
    assert outcome["result"]["feeds_improvement_memory"] is True


def test_reconciler_delegates_bounded_state_timeouts():
    class ReconcileStore:
        def reconcile(self, **kwargs):
            return {"reconciled": 3, "received": kwargs}

    result = RecoveryControlPlane().reconcile(
        ReconcileStore(),
        now=1000,
        approved_timeout_seconds=10,
        executing_timeout_seconds=20,
        dispatched_timeout_seconds=30,
    )

    assert result["reconciled"] == 3
    assert result["received"]["now"] == 1000
