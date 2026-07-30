from __future__ import annotations

from types import SimpleNamespace

from assistx.strict_offline_projection import install_strict_offline_projection


def test_projection_filters_public_paperclip_and_remote_services(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_STRICT_OFFLINE", "true")
    module = SimpleNamespace(
        _strict_offline_projection_installed=False,
        _provider_projection=lambda _base: [
            {
                "provider_id": "assistx",
                "local": True,
                "lane": "local",
                "services": [{"url": "http://api:8000"}],
            },
            {
                "provider_id": "cerebras",
                "local": False,
                "lane": "free_api",
                "can_use_free_api": True,
            },
            {
                "provider_id": "paperclip",
                "local": True,
                "lane": "paperclip",
            },
        ],
        _node_projection=lambda _base, _graph: [
            {"node_id": "assistx-api", "lane": "local", "services": []},
            {"node_id": "paperclip", "lane": "paperclip", "services": []},
        ],
        _service_projection=lambda _base: [
            {"service_id": "assistx", "url": "http://api:8000"},
            {"service_id": "remote", "url": "https://api.example.com/v1"},
        ],
        _merge_providers=lambda static, live: static + live,
    )

    install_strict_offline_projection(module)

    providers = module._provider_projection("http://api:8000")
    nodes = module._node_projection("http://api:8000", {})
    services = module._service_projection("http://api:8000")
    merged = module._merge_providers(
        providers,
        [
            {
                "provider_id": "openrouter",
                "local": False,
                "services": [{"url": "https://openrouter.ai/api/v1"}],
            }
        ],
    )

    assert [item["provider_id"] for item in providers] == ["assistx"]
    assert [item["node_id"] for item in nodes] == ["assistx-api"]
    assert [item["service_id"] for item in services] == ["assistx"]
    assert [item["provider_id"] for item in merged] == ["assistx"]
