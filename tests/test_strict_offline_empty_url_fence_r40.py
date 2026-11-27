"""Round 40: strict-offline projection must deny unverifiable endpoints.

An empty or missing service URL cannot be shown to be private.  The old
``_private_url`` returned True for that case, so providers, nodes and services
with no endpoint survived the strict-offline allowlist and were advertised to
auto-router with an unverified destination.
"""

from __future__ import annotations

from types import SimpleNamespace

from assistx import strict_offline_projection as sop


def _install(monkeypatch, *, providers, nodes, services):
    monkeypatch.setenv("ASSISTX_STRICT_OFFLINE", "true")
    module = SimpleNamespace(
        _strict_offline_projection_installed=False,
        _provider_projection=lambda _base: providers,
        _node_projection=lambda _base, _graph: nodes,
        _service_projection=lambda _base: services,
        _merge_providers=lambda static, live: list(static) + list(live),
    )
    sop.install_strict_offline_projection(module)
    return module


def test_absent_url_is_not_private():
    assert sop._private_url("") is False
    assert sop._private_url(None) is False
    assert sop._private_url("   ") is False


def test_private_and_public_urls_keep_existing_verdicts():
    assert sop._private_url("http://api:8000") is True
    assert sop._private_url("bolt://neo4j:7687") is True
    assert sop._private_url("http://100.69.158.114:1234") is True
    assert sop._private_url("https://api.example.com/v1") is False
    assert sop._private_url("ftp://api:8000") is False


def test_provider_with_empty_service_url_is_denied(monkeypatch):
    module = _install(
        monkeypatch,
        providers=[
            {
                "provider_id": "verified",
                "local": True,
                "lane": "local",
                "services": [{"url": "http://api:8000"}],
            },
            {
                "provider_id": "empty-url",
                "local": True,
                "lane": "local",
                "services": [{"url": ""}],
            },
            {
                "provider_id": "missing-url",
                "local": True,
                "lane": "local",
                "services": [{}],
            },
        ],
        nodes=[],
        services=[],
    )
    assert [
        item["provider_id"] for item in module._provider_projection("http://api:8000")
    ] == ["verified"]


def test_node_with_unverifiable_service_url_is_denied(monkeypatch):
    module = _install(
        monkeypatch,
        providers=[],
        nodes=[
            {
                "node_id": "verified-node",
                "lane": "local",
                "services": [{"url": "http://api:8000"}],
            },
            {
                "node_id": "empty-url-node",
                "lane": "local",
                "services": [{"url": "   "}],
            },
            {"node_id": "no-service-url-node", "lane": "local", "services": [{}]},
        ],
        services=[],
    )
    assert [
        item["node_id"] for item in module._node_projection("http://api:8000", {})
    ] == ["verified-node"]


def test_service_with_unverifiable_url_is_denied(monkeypatch):
    module = _install(
        monkeypatch,
        providers=[],
        nodes=[],
        services=[
            {"service_id": "verified", "url": "http://api:8000"},
            {"service_id": "empty", "url": ""},
            {"service_id": "missing"},
            {"service_id": "public", "url": "https://api.example.com/v1"},
        ],
    )
    assert [
        item["service_id"] for item in module._service_projection("http://api:8000")
    ] == ["verified"]


def test_merged_live_provider_with_empty_url_is_denied(monkeypatch):
    module = _install(
        monkeypatch,
        providers=[
            {
                "provider_id": "verified",
                "local": True,
                "lane": "local",
                "services": [{"url": "http://api:8000"}],
            }
        ],
        nodes=[],
        services=[],
    )
    merged = module._merge_providers(
        module._provider_projection("http://api:8000"),
        [
            {
                "provider_id": "unverifiable-live",
                "local": True,
                "lane": "local",
                "services": [{"url": ""}],
            }
        ],
    )
    assert [item["provider_id"] for item in merged] == ["verified"]


def test_strict_offline_disabled_leaves_projection_untouched(monkeypatch):
    monkeypatch.setenv("ASSISTX_STRICT_OFFLINE", "false")
    module = SimpleNamespace(
        _strict_offline_projection_installed=False,
        _provider_projection=lambda _base: [{"provider_id": "untouched"}],
        _node_projection=lambda _base, _graph: [],
        _service_projection=lambda _base: [],
        _merge_providers=lambda static, live: list(static) + list(live),
    )
    sop.install_strict_offline_projection(module)
    assert module._provider_projection("http://api:8000") == [{"provider_id": "untouched"}]
