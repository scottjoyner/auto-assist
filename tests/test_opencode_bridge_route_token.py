"""The Opencode bridge must not emit unauthenticated router decision events."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts/opencode-bridge/router_pick.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("router_pick_test", FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_fails_before_io_without_decision_token(monkeypatch, capsys):
    bridge = load_bridge()
    monkeypatch.delenv("AUTO_ROUTER_ROUTE_DECISION_TOKEN", raising=False)
    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("unexpected HTTP"))
    with pytest.raises(SystemExit) as exc:
        bridge.route({"correlation_id": "synthetic"})
    assert exc.value.code == 2
    assert "token missing" in capsys.readouterr().err.lower()


def test_route_uses_scoped_bearer_token_without_logging(monkeypatch, capsys):
    bridge = load_bridge()
    secret = "synthetic-route-decision-only"
    monkeypatch.setenv("AUTO_ROUTER_ROUTE_DECISION_TOKEN", secret)

    class DummyResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"lane":"blocked","model":"none"}'

    def fake_urlopen(request, timeout):
        assert request.get_header("Authorization") == "Bearer " + secret
        assert request.full_url.endswith("/api/routes/request")
        return DummyResponse()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    assert bridge.route({"correlation_id": "synthetic"})["lane"] == "blocked"
    assert secret not in capsys.readouterr().err


def test_main_never_syncs_without_route_token(monkeypatch, capsys):
    bridge = load_bridge()
    monkeypatch.delenv("AUTO_ROUTER_ROUTE_DECISION_TOKEN", raising=False)
    monkeypatch.setattr(bridge, "run_sync", lambda **kw: pytest.fail("sync must not run"))
    monkeypatch.setattr(bridge, "route", lambda *a: pytest.fail("route must not run"))
    monkeypatch.setattr(bridge.sys, "argv", ["router_pick.py"])
    assert bridge.main() == 2
    assert "refusing sync" in capsys.readouterr().err
