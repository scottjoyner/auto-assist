from __future__ import annotations

import pytest

from assistx import runtime_projection


SECRET = "projection-secret"


def complete_runtime():
    return {
        "runtime_instance_id": "lmstudio-xwing-1234",
        "node_id": "xwing",
        "runtime_kind": "lmstudio",
        "runtime_version": "0.4.7",
        "headless": False,
        "process_id": "4242",
        "updated_at_ts": 999_000,
        "loaded_models": [
            {
                "admitted": True,
                "expires_at_ts": 1_060_000,
                "model_instance_id": "model-xwing-1",
                "model_key": "local/qwen",
                "provider_model": "qwen.gguf",
                "artifact_fingerprint": "sha256:abcdef",
                "quantization": "Q4_K_M",
                "context_length": 32768,
                "capabilities_json": [
                    "chat",
                    "streaming",
                    "code",
                    "tool_use",
                ],
            }
        ],
    }


def approved_paths():
    return [
        {
            "runtime_instance_id": "lmstudio-xwing-1234",
            "base_url": "http://192.168.1.9:1234/v1",
            "transport": "lan",
            "preference": 10,
            "approved_by": "operator",
            "approval_id": "approval-path-lan",
        },
        {
            "runtime_instance_id": "lmstudio-xwing-1234",
            "base_url": "http://100.64.0.9:1234/v1",
            "transport": "tailscale",
            "preference": 20,
            "approved_by": "operator",
            "approval_id": "approval-path-tailnet",
        },
    ]


def approved_capacity():
    return [
        {
            "runtime_instance_id": "lmstudio-xwing-1234",
            "parallel_slots": 1,
            "queue_limit": 4,
            "queue_timeout_seconds": 30,
            "approved_by": "operator",
            "approval_id": "approval-capacity",
        }
    ]


def install_fixtures(monkeypatch, runtime=None, paths=None, capacity=None):
    monkeypatch.setattr(
        runtime_projection,
        "_projection_state",
        lambda _factory: {
            "generation": 7,
            "revision": "fleet-7",
            "status": "approved",
            "approved_by": "operator",
            "approval_id": "approval-generation",
        },
    )
    monkeypatch.setattr(
        runtime_projection,
        "_runtime_rows",
        lambda _factory, _now: [runtime or complete_runtime()],
    )
    monkeypatch.setattr(
        runtime_projection,
        "_access_rows",
        lambda _factory, _now: approved_paths() if paths is None else paths,
    )
    monkeypatch.setattr(
        runtime_projection,
        "_capacity_rows",
        lambda _factory, _now: approved_capacity() if capacity is None else capacity,
    )


def test_projection_contains_resolved_physical_identity_and_ordered_paths(monkeypatch):
    install_fixtures(monkeypatch)

    document = runtime_projection.build_runtime_projection(
        lambda: None,
        secret=SECRET,
        ttl_seconds=60,
        now_ms=1_000_000,
    )

    assert document["generation"] == 7
    assert document["revision"] == "fleet-7"
    assert document["checksum"] == runtime_projection.projection_checksum(document)
    assert document["signature"] == runtime_projection.projection_signature(
        7,
        document["checksum"],
        SECRET,
    )
    provider = document["providers"][0]
    assert provider["runtime_instance_id"] == "lmstudio-xwing-1234"
    assert provider["runtime_version"] == "0.4.7"
    assert provider["parallel_slots"] == 1
    assert provider["access_urls"] == [
        "http://192.168.1.9:1234/v1",
        "http://100.64.0.9:1234/v1",
    ]
    model = provider["models"][0]
    assert model["model_instance_id"] == "model-xwing-1"
    assert model["artifact_fingerprint"] == "sha256:abcdef"
    assert model["quantization"] == "Q4_K_M"
    assert model["context_window"] == 32768


def test_projection_fails_closed_without_capacity_or_complete_model(monkeypatch):
    install_fixtures(monkeypatch, capacity=[])
    with pytest.raises(
        runtime_projection.RuntimeProjectionBlocked,
        match="no fully approved",
    ):
        runtime_projection.build_runtime_projection(
            lambda: None,
            secret=SECRET,
            now_ms=1_000_000,
        )

    incomplete = complete_runtime()
    incomplete["loaded_models"][0]["artifact_fingerprint"] = "unknown"
    install_fixtures(monkeypatch, runtime=incomplete)
    with pytest.raises(
        runtime_projection.RuntimeProjectionBlocked,
        match="no fully approved",
    ):
        runtime_projection.build_runtime_projection(
            lambda: None,
            secret=SECRET,
            now_ms=1_000_000,
        )


def test_projection_requires_shared_hmac_secret(monkeypatch):
    install_fixtures(monkeypatch)

    with pytest.raises(
        runtime_projection.RuntimeProjectionBlocked,
        match="HMAC secret",
    ):
        runtime_projection.build_runtime_projection(
            lambda: None,
            secret="",
            now_ms=1_000_000,
        )
