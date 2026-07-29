import time

import pytest

from assistx.loadout_control import LoadoutControlPlane, proposal_fingerprint


class Store:
    def __init__(self):
        self.value = None

    def create(self, action, actor):
        self.value = {
            "id": "proposal",
            "action": action,
            "fingerprint": proposal_fingerprint(action),
            "status": "PROPOSED",
            "expires_at_ts": int(time.time()) + 300,
        }
        return self.value

    def get(self, proposal_id):
        return dict(self.value) if self.value else None

    def transition(self, proposal_id, expected_status, status, actor, metadata=None):
        if not self.value or self.value["status"] != expected_status:
            return None
        self.value["status"] = status
        self.value["result"] = metadata or {}
        return dict(self.value)


def action(kind="replicate_candidate"):
    return {
        "action": kind,
        "node_id": "x1",
        "model_id": "qwen",
        "requires_approval": True,
    }


def node_map(loaded=None, available=None):
    return {"nodes": [{
        "id": "x1", "ip": "100.64.43.123", "online": True, "report_fresh": True,
        "loaded_models": loaded or [], "all_models": available or ["qwen"],
    }]}


def test_exact_fingerprint_is_required(monkeypatch):
    control = LoadoutControlPlane()
    store = Store()
    proposal = control.propose(store, action(), "operator")

    with pytest.raises(ValueError, match="fingerprint"):
        control.approve(store, proposal["id"], "wrong", "operator")

    approved = control.approve(store, proposal["id"], proposal["fingerprint"], "operator")
    assert approved["status"] == "APPROVED"


def test_execution_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSISTX_LOADOUT_EXECUTION_ENABLED", raising=False)
    control = LoadoutControlPlane()
    store = Store()
    proposal = control.propose(store, action(), "operator")
    control.approve(store, proposal["id"], proposal["fingerprint"], "operator")

    result = control.execute(store, proposal["id"], "operator", node_map(), lambda *_: {"ok": True}, lambda *_: {"ok": True}, lambda *_: {})

    assert result == {"executed": False, "blocked": True, "reason": "execution_disabled"}


def test_failed_verification_rolls_back(monkeypatch):
    monkeypatch.setenv("ASSISTX_LOADOUT_EXECUTION_ENABLED", "true")
    control = LoadoutControlPlane()
    store = Store()
    proposal = control.propose(store, action(), "operator")
    control.approve(store, proposal["id"], proposal["fingerprint"], "operator")
    calls = []

    def load(url, model):
        calls.append(("load", url, model))
        return {"ok": True}

    def unload(url, model):
        calls.append(("unload", url, model))
        return {"ok": True}

    result = control.execute(store, proposal["id"], "operator", node_map(), load, unload, lambda _: {"loaded_models": []})

    assert result["rolled_back"] is True
    assert calls[0][0] == "load"
    assert calls[1][0] == "unload"
    assert store.value["status"] == "ROLLED_BACK"


def test_unload_revalidates_only_resident_model(monkeypatch):
    monkeypatch.setenv("ASSISTX_LOADOUT_EXECUTION_ENABLED", "true")
    control = LoadoutControlPlane()
    store = Store()
    proposal = control.propose(store, action("unload_candidate"), "operator")
    control.approve(store, proposal["id"], proposal["fingerprint"], "operator")

    result = control.execute(
        store, proposal["id"], "operator",
        node_map(loaded=["qwen"], available=["qwen"]),
        lambda *_: {"ok": True}, lambda *_: {"ok": True}, lambda *_: {},
    )

    assert result["blocked"] is True
    assert "only resident" in result["reason"]
