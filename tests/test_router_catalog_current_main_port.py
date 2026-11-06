"""Synthetic tests for current-main Control Room -> Router snapshot migration."""
from __future__ import annotations

from urllib.error import HTTPError

from assistx import control_room

_EFFECTIVE = {
    "schema_version": 1,
    "kind": "EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
    "state_source": "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
    "no_probes_or_dispatch": True,
    "may_prove_model_loaded": False,
    "may_modify_routes": False,
    "provider_count": 1,
    "providers": [{
        "provider_id": "synthetic-1234", "node_id": "XWING",
        "enabled_in_router_memory": True,
        "effective_base_url": {"status": "PARSED_NOT_PROBED", "hostname": "100.64.0.8"},
        "effective_access_urls": [],
        "configured_models": [{"provider_model": "LFM3-1.7B"}],
    }],
}


def test_current_main_catalog_prefers_sanitized_effective(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router.test:8088")
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", raising=False)
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-token")
    seen = []

    def fetch(url, path, *, admin_token):
        seen.append((url, admin_token))
        return _EFFECTIVE, 1.0

    monkeypatch.setattr(control_room, "_http_json", fetch)
    result = control_room._provider_catalog()
    assert result["xwing"] == ["LFM3-1.7B"]
    assert seen == [("http://router.test:8088/admin/providers/effective", "synthetic-token")]


def test_current_main_wrong_token_never_downgrades(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router.test:8088")
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", raising=False)
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "bad-token")
    seen = []

    def fetch(url, path, *, admin_token):
        seen.append(url)
        raise HTTPError(url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(control_room, "_http_json", fetch)
    assert control_room._provider_catalog() == {}
    assert seen == ["http://router.test:8088/admin/providers/effective"]


def test_current_main_old_router_404_only_authenticated_fallback(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router.test:8088")
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", raising=False)
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-token")
    seen = []

    def fetch(url, path, *, admin_token):
        seen.append((url, admin_token))
        if url.endswith("/effective"):
            raise HTTPError(url, 404, "not found", {}, None)
        return {"providers": [{"enabled": True, "node_id": "XWING", "models": [{"alias": "LFM3-1.7B"}]}]}, 1.0

    monkeypatch.setattr(control_room, "_http_json", fetch)
    assert control_room._provider_catalog()["xwing"] == ["LFM3-1.7B"]
    assert seen == [
        ("http://router.test:8088/admin/providers/effective", "synthetic-token"),
        ("http://router.test:8088/admin/providers", "synthetic-token"),
    ]


def test_current_main_override_requires_same_base_origin(monkeypatch):
    monkeypatch.delenv("AUTO_ROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", "http://other-host:8088/admin/providers")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-token")
    seen = []
    monkeypatch.setattr(control_room, "_http_json", lambda *args, **kwargs: seen.append((args, kwargs)))
    assert control_room._provider_catalog() == {}
    assert seen == []


def test_admin_transport_bounded_read(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, count=None):
            seen.append(count)
            return b"{}"

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(control_room, "build_opener", lambda *_args: Opener())
    assert control_room._http_json("http://router.test:8088", "/admin/providers", admin_token="synthetic-token")[0] == {}
    assert seen == [4 * 1024 * 1024 + 1]


def test_admin_transport_rejects_oversized_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, count=None):
            assert count == 4 * 1024 * 1024 + 1
            return b"x" * count
    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()
    monkeypatch.setattr(control_room, "build_opener", lambda *_args: Opener())
    import pytest
    with pytest.raises(ValueError, match="router_admin_response_size_exceeded"):
        control_room._http_json("http://router.test:8088", "/admin/providers", admin_token="synthetic-token")
