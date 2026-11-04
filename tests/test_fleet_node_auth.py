"""Synthetic regression tests for the fleet-node-agent node identity path.

Focused, resource-light tests: no containers, no network, no real credentials.
They pin two observed fleet blockers as of 2026-11-03:

* fleet nodes presenting their per-node credential were rejected with HTTP 401
  because the task list/claim path only accepted general Basic auth;
* operationally healthy nodes are not admitted anywhere: the signed runtime
  projection is expired and zero slots are approved, so admission stays
  fail-closed (and this fix does not change that).
"""
from __future__ import annotations

import json

import pytest

from assistx import fleet_node_agent, fleet_node_auth, work_supply

_SYNTHETIC_TOKENS = {"node-a": "synthetic-token-a", "node-b": "synthetic-token-b"}


@pytest.fixture
def node_tokens(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    monkeypatch.setenv("FLEET_NODE_TOKEN", _SYNTHETIC_TOKENS["node-a"])
    monkeypatch.setenv("FLEET_NODE_ID", "node-a")


def test_no_node_headers_defers(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    assert fleet_node_auth.evaluate_fleet_node_identity(None, None) is None


def test_partial_headers_fail_closed(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    with pytest.raises(fleet_node_auth.FleetNodeAuthHold):
        fleet_node_auth.evaluate_fleet_node_identity("node-a", None)
    with pytest.raises(fleet_node_auth.FleetNodeAuthHold):
        fleet_node_auth.evaluate_fleet_node_identity(None, "synthetic-token-a")


def test_unregistered_node_fail_closed(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    with pytest.raises(fleet_node_auth.FleetNodeAuthHold):
        fleet_node_auth.evaluate_fleet_node_identity("intruder", "synthetic-token-a")


def test_wrong_token_fail_closed(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    with pytest.raises(fleet_node_auth.FleetNodeAuthHold):
        fleet_node_auth.evaluate_fleet_node_identity("node-a", "wrong-token")


def test_registered_node_identity_ok(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    assert fleet_node_auth.evaluate_fleet_node_identity("node-a", _SYNTHETIC_TOKENS["node-a"]) == "node-a"


def test_missing_or_malformed_registry_fail_closed(monkeypatch):
    monkeypatch.delenv("ASSISTX_FLEET_NODE_TOKENS", raising=False)
    with pytest.raises(fleet_node_auth.FleetNodeAuthHold):
        fleet_node_auth.evaluate_fleet_node_identity("node-a", _SYNTHETIC_TOKENS["node-a"])
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", "not-json")
    with pytest.raises(fleet_node_auth.FleetNodeAuthHold):
        fleet_node_auth.evaluate_fleet_node_identity("node-a", _SYNTHETIC_TOKENS["node-a"])


def test_binding_error_only_for_node_auth(monkeypatch):
    assert fleet_node_auth.fleet_node_binding_error(None, "node-b") is None
    assert fleet_node_auth.fleet_node_binding_error("node-a", "node-b") == "fleet node identity mismatch"
    assert fleet_node_auth.fleet_node_binding_error("node-a", "node-a") is None
    assert fleet_node_auth.fleet_node_binding_error("node-a", None) == "fleet node identity mismatch"


def test_node_agent_poll_attaches_node_token(monkeypatch, node_tokens):
    captured = {}

    def fake_http(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return 200, {"items": []}

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    worked = fleet_node_agent._poll_once(
        assistx_url="http://assistx.internal:8000",
        router_url="http://router.internal:8088",
        auth=None,
        node_id="node-a",
        caps=["script"],
        lmstudio_url=None,
    )
    assert worked is False
    headers = captured["kwargs"]["headers"]
    assert headers["X-Fleet-Node-Id"] == "node-a"
    assert headers["X-Fleet-Node-Token"] == _SYNTHETIC_TOKENS["node-a"]
    assert "agent_id=node-a" in captured["url"]


def test_node_agent_omits_node_token_when_unset(monkeypatch):
    monkeypatch.delenv("FLEET_NODE_TOKEN", raising=False)
    assert fleet_node_agent._node_auth_headers() == {}


def test_node_agent_omits_token_when_node_id_missing(monkeypatch):
    monkeypatch.setenv("FLEET_NODE_TOKEN", "synthetic-token-a")
    monkeypatch.delenv("FLEET_NODE_ID", raising=False)
    monkeypatch.setattr(fleet_node_agent, "_detect_hostname", lambda: "")
    assert fleet_node_agent._node_auth_headers() == {}


def test_work_supply_zero_slots_when_projection_unavailable(monkeypatch):
    from assistx.runtime_projection import RuntimeProjectionBlocked

    monkeypatch.setattr(
        work_supply,
        "build_runtime_projection",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeProjectionBlocked("synthetic projection expired")
        ),
    )
    decision = work_supply.compute_work_supply_decision(neo_factory=lambda: pytest.fail("no DB"))
    assert decision.total_slots == 0
    assert decision.free_slots == 0
    assert decision.allow_background is False


def test_projection_marks_unadmitted_node_blocked():
    from assistx import fleet_context_projection

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def run(self, *_args, **_kwargs):
            return [
                {
                    "node": {
                        "node_id": "xwing",
                        "display_name": "xwing",
                        "worker_mode": "observer_only",
                        "allow_agent_runtime": False,
                        "allow_code_execution": False,
                        "tailnet_discovered": True,
                        "online": True,
                        "capabilities_json": '["script"]',
                        "roles_json": "[]",
                        "tailscale_ips_json": "[]",
                    }
                }
            ]

    class FakeNeo:
        def _session(self):
            return FakeSession()

        def close(self):
            return None

    nodes = fleet_context_projection.fleet_nodes(FakeNeo)
    assert len(nodes) == 1
    assert nodes[0]["running"] is True
    assert nodes[0]["lane"] == "blocked"
    assert nodes[0]["can_use_free_api"] is False
