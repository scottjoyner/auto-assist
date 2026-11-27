"""Fail-closed guard for the loadout control plane's operation result.

The load/unload callable is an external runtime boundary.  A malformed
``ok`` value (a stringified flag, an error string, a status code, a list or
a dict) must never certify a mutation as verified.  Only a genuine JSON
boolean ``true`` is accepted.

All state here is in-memory: an in-process Store plus lambda callables.
No socket, service, Neo4j, SQLite/DB/WAL, credential file, container,
network mount or model load is touched.
"""

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


def _action(kind="replicate_candidate"):
    return {
        "action": kind,
        "node_id": "x1",
        "model_id": "qwen",
        "requires_approval": True,
    }


def _node_map(loaded=None, available=None):
    return {"nodes": [{
        "id": "x1", "ip": "100.64.43.123", "online": True,
        "report_fresh": True,
        "loaded_models": loaded or [],
        "all_models": available or ["qwen"],
    }]}


def _approved(monkeypatch, kind="replicate_candidate"):
    monkeypatch.setenv("ASSISTX_LOADOUT_EXECUTION_ENABLED", "true")
    control = LoadoutControlPlane()
    store = Store()
    proposal = control.propose(store, _action(kind), "operator")
    control.approve(store, proposal["id"], proposal["fingerprint"], "operator")
    return control, store, proposal["id"]


# Truthy non-boolean ``ok`` values that a malformed runtime response can
# emit.  Python truthiness accepts all of these, so a failed mutation would
# be certified as verified and the rollback skipped.
_MALFORMED_OK = [
    "false",
    "0",
    "off",
    "error: model not loaded",
    "true",
    1,
    2,
    1.0,
    [1],
    {"status": "failed"},
]


@pytest.mark.parametrize("bad_ok", _MALFORMED_OK)
def test_truthy_non_boolean_ok_fails_closed(monkeypatch, bad_ok):
    control, store, proposal_id = _approved(monkeypatch)
    calls = []

    def load(url, model):
        calls.append("load")
        return {"ok": bad_ok}

    def unload(url, model):
        calls.append("unload")
        return {"ok": True}

    # Verification shows the expected post-mutation state; the operation
    # itself reported failure via a malformed ``ok``.  The gate must not
    # certify this as a successful, verified mutation.
    result = control.execute(
        store, proposal_id, "operator", _node_map(),
        load, unload, lambda _: {"loaded_models": ["qwen"]},
    )

    assert result["executed"] is True
    assert result["verified"] is False, (
        f"malformed ok={bad_ok!r} was certified as verified"
    )
    assert result.get("rolled_back") is True
    assert store.value["status"] == "ROLLED_BACK"
    assert calls == ["load", "unload"], "rollback callable was not invoked"


@pytest.mark.parametrize("bad_ok", _MALFORMED_OK)
def test_truthy_non_boolean_ok_fails_closed_on_unload(monkeypatch, bad_ok):
    control, store, proposal_id = _approved(monkeypatch, "unload_candidate")
    calls = []

    def load(url, model):
        calls.append("load")
        return {"ok": True}

    def unload(url, model):
        calls.append("unload")
        return {"ok": bad_ok}

    result = control.execute(
        store, proposal_id, "operator",
        _node_map(loaded=["qwen", "other"], available=["qwen", "other"]),
        load, unload, lambda _: {"loaded_models": ["other"]},
    )

    assert result["verified"] is False
    assert result.get("rolled_back") is True
    assert store.value["status"] == "ROLLED_BACK"
    assert calls == ["unload", "load"], "rollback callable was not invoked"


def test_genuine_boolean_true_still_completes(monkeypatch):
    """Control: a real boolean true must still certify a good mutation."""
    control, store, proposal_id = _approved(monkeypatch)
    result = control.execute(
        store, proposal_id, "operator", _node_map(),
        lambda *_: {"ok": True},
        lambda *_: {"ok": True},
        lambda _: {"loaded_models": ["qwen"]},
    )
    assert result["verified"] is True
    assert result["executed"] is True
    assert store.value["status"] == "COMPLETED"


@pytest.mark.parametrize("ok_value", [False, None])
def test_boolean_false_and_missing_ok_still_roll_back(monkeypatch, ok_value):
    """Control: existing fail-closed behaviour for false/missing is kept."""
    control, store, proposal_id = _approved(monkeypatch)
    result = control.execute(
        store, proposal_id, "operator", _node_map(),
        lambda *_: {"ok": ok_value},
        lambda *_: {"ok": True},
        lambda _: {"loaded_models": ["qwen"]},
    )
    assert result["verified"] is False
    assert result.get("rolled_back") is True
    assert store.value["status"] == "ROLLED_BACK"
