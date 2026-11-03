"""Offline regressions for opt-in router admin client.

Never opens a socket; all calls are synthetic and strictly mocked.
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/opencode-bridge/sync_configs_to_assistx.py"
spec = importlib.util.spec_from_file_location("safe_opencode_bridge", SOURCE)
bridge = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(bridge)


@pytest.mark.parametrize(
    "value",
    [
        "", "file:///etc/shadow", "http://8.8.8.8:8088",
        "http://10.0.0.20:8088", "http://router.example:8088",
        "http://user:password@100.64.43.123:8088",
        "http://100.64.43.123:8088/v1", "http://100.64.43.123:8088/?secret=1",
        "http://100.64.43.123:8088/#fragment", "http://100.64.43.123:99999",
        "https://user:password@example.com:8088",
        "http://100.64.43.123:8088@evil.invalid:8088",
    ],
)
def test_admin_refresh_rejects_unsafe_transport_without_echoing_input(value):
    with pytest.raises(bridge.RouterAdminRefreshError) as err:
        bridge._validated_router_refresh_url(value)
    assert str(err.value) == "router_admin_transport_not_approved"
    assert "password" not in str(err.value)
    assert "secret" not in str(err.value)


@pytest.mark.parametrize(
    "base",
    ["http://127.0.0.1:8088", "http://localhost:8088/",
     "http://100.64.43.123:8088", "https://router.example:8443"],
)
def test_admin_refresh_accepts_explicit_local_tailnet_or_tls(base):
    assert bridge._validated_router_refresh_url(base).endswith(
        "/admin/live-models/refresh"
    )


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, n):
        assert n == 8193
        return b'{"status":"synthetic-ok"}'


class FakeOpener:
    def __init__(self):
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return FakeResponse()


def test_privileged_post_uses_only_admin_header_without_inherited_proxies(monkeypatch):
    opener = FakeOpener()
    builders = []

    def build(*handlers):
        builders.extend(handlers)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build)
    token = "synthetic-secret-admin-token"
    target = bridge._validated_router_refresh_url("http://100.64.43.123:8088")
    bridge._authenticated_router_refresh(target, token)
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert timeout == 8
    assert request.method == "POST"
    assert request.full_url == target
    assert request.get_header("X-admin-token") == token
    assert request.get_header("Authorization") is None
    assert request.data == b"{}"
    assert any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {}
               for h in builders)
    assert any(isinstance(h, bridge._NoAdminRedirect) for h in builders)
    assert bridge._NoAdminRedirect().redirect_request(
        request, None, 302, "Moved", {}, "https://evil.invalid"
    ) is None


@pytest.mark.parametrize(
    "code,expected",
    [(302, "router_admin_redirect_denied"), (401, "router_admin_http_401"),
     (403, "router_admin_http_403"), (404, "router_admin_http_404"),
     (500, "router_admin_http_500")],
)
def test_admin_transport_failures_redact_response_body_and_token(
    monkeypatch, code, expected,
):
    class ErrorOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, code, "response includes private token",
                {"Location": "https://evil.invalid/private"},
                io.BytesIO(b"BODY_WITH_SECRET"),
            )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: ErrorOpener())
    with pytest.raises(bridge.RouterAdminRefreshError) as exc:
        bridge._authenticated_router_refresh(
            "http://100.64.43.123:8088/admin/live-models/refresh",
            "really-secret-admin-token",
        )
    assert str(exc.value) == expected
    assert "secret" not in str(exc.value)


def test_preflight_missing_admin_token_prevents_any_side_effect(monkeypatch, tmp_path):
    config = {"provider": {
        "remote": {"name": "Synthetic",
                   "options": {"baseURL": "http://100.108.99.47:1234/v1"}}
    }}
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text(json.dumps(config))
    monkeypatch.setattr(bridge, "load_yaml", lambda path: {})
    def forbidden(*args, **kwargs):
        raise AssertionError("no registration, probe or privileged POST permitted")
    monkeypatch.setattr(bridge, "_post", forbidden)
    monkeypatch.setattr(bridge, "_authenticated_router_refresh", forbidden)
    with pytest.raises(bridge.RouterAdminRefreshError, match="router_admin_token_required"):
        bridge.run_sync(
            password="synthetic-pass", refresh_router=True, admin_token="",
            opencode_config=str(cfgpath), x1_mirror=str(tmp_path),
        )


def test_default_sync_never_attempts_privileged_refresh(monkeypatch, tmp_path):
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text(json.dumps({"provider": {
        "remote": {"name": "Synthetic",
                   "options": {"baseURL": "http://100.108.99.47:1234/v1"}}
    }}))
    monkeypatch.setattr(bridge, "load_yaml", lambda path: {})
    calls = []
    monkeypatch.setattr(bridge, "_post",
                        lambda url, payload, user, password: calls.append(url) or {"ok": True})
    monkeypatch.setattr(
        bridge, "_authenticated_router_refresh",
        lambda *args: (_ for _ in ()).throw(AssertionError("admin refresh must be opt-in")),
    )
    assert bridge.run_sync(
        password="synthetic", opencode_config=str(cfgpath),
        x1_mirror=str(tmp_path),
    ) == 1
    assert len(calls) == 2
    assert all("/api/swarm/model-endpoints/" in url for url in calls)


def test_explicit_refresh_uses_validated_target_and_supplied_admin_token(
    monkeypatch, tmp_path,
):
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text('{"provider": {}}')
    monkeypatch.setattr(bridge, "load_yaml", lambda path: {})
    calls = []
    monkeypatch.setattr(
        bridge, "_authenticated_router_refresh",
        lambda url, token: calls.append((url, token)),
    )
    assert bridge.run_sync(
        password="synthetic", refresh_router=True,
        admin_token="synthetic-admin-secret",
        opencode_config=str(cfgpath), x1_mirror=str(tmp_path),
    ) == 0
    assert calls == [(
        "http://100.64.43.123:8088/admin/live-models/refresh",
        "synthetic-admin-secret",
    )]


def test_explicit_refresh_rejects_unsafe_target_before_registration(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "load_yaml", lambda path: {})
    monkeypatch.setattr(bridge, "_post", lambda *a: (_ for _ in ()).throw(
        AssertionError("must not invoke AssistX mutation")
    ))
    with pytest.raises(bridge.RouterAdminRefreshError, match="transport_not_approved"):
        bridge.run_sync(
            password="synthetic", refresh_router=True, admin_token="synthetic-secret",
            auto_router_url="http://some-untrusted-host.invalid:8088",
            opencode_config=str(tmp_path / "nonexistent.json"),
            x1_mirror=str(tmp_path),
        )


def test_dry_run_never_sends_admin_token_or_mutates_even_if_requested(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(bridge, "load_yaml", lambda path: {})
    monkeypatch.setattr(bridge, "_post", lambda *a: (_ for _ in ()).throw(
        AssertionError("no network calls permitted")
    ))
    monkeypatch.setattr(bridge, "_authenticated_router_refresh", lambda *a: (
        _ for _ in ()).throw(AssertionError("no privileged calls permitted")
    ))
    assert bridge.run_sync(
        dry_run=True, refresh_router=True, password="",
        admin_token="synthetic-secret",
        opencode_config=str(tmp_path / "missing.json"), x1_mirror=str(tmp_path),
    ) == 0
