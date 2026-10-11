"""Strict isolated regression tests for read-only router admin script probes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from router_admin_probe import probe_router_context  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = {"a": 1} if data is None else data

    def json(self):
        return self._data


class FakeRequests:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_no_admin_token_never_probes_privileged_endpoint(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_TOKEN", raising=False)
    transport = FakeRequests()
    assert probe_router_context(transport) == {"status": "auth_required", "has_context": None}
    assert not transport.calls


@pytest.mark.parametrize("status", [401, 403])
def test_bad_admin_credential_never_downgrades_to_anonymous(monkeypatch, status):
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-test-only")
    transport = FakeRequests(FakeResponse(status))
    result = probe_router_context(transport)
    assert result["status"] == "unauthorized"
    assert result["has_context"] is None
    assert len(transport.calls) == 1
    url, kwargs = transport.calls[0]
    assert url == "http://localhost:8088/admin/context"
    assert kwargs["headers"] == {"X-Admin-Token": "synthetic-test-only"}
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == 5


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_rejected_without_following(monkeypatch, status):
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-test-only")
    transport = FakeRequests(FakeResponse(status))
    assert probe_router_context(transport)["status"] == "redirect_rejected"
    assert len(transport.calls) == 1
    assert transport.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("payload", [[], {}, {"context": True}])
def test_authenticated_read_only_response_does_not_copy_contents(monkeypatch, payload):
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-test-only")
    transport = FakeRequests(FakeResponse(200, payload))
    assert probe_router_context(transport) == {"status": "ok", "has_context": bool(payload)}


@pytest.mark.parametrize("status", [404, 500, 503])
def test_non_success_is_not_a_healthy_context(monkeypatch, status):
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-test-only")
    transport = FakeRequests(FakeResponse(status))
    assert probe_router_context(transport)["status"] == "error"


def test_transport_failure_has_no_error_body_or_token(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-test-only")

    class BrokenRequests:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("token should never appear in returned diagnostics")

    assert probe_router_context(BrokenRequests()) == {"status": "unreachable", "has_context": None}
