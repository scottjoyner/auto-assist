from __future__ import annotations

import pytest

from assistx import runtime_projection


SECRET = "projection-secret"


def complete_runtime(*, expires_at_ts: int = 1_060_000):
    return {
        "runtime_instance_id": "lmstudio-xwing-1234",
        "node_id": "xwing",
        "runtime_kind": "lmstudio",
        "runtime_version": "0.4.7",
        "headless": False,
        "process_id": "4242",
        "updated_at_ts": 999_000,
        "expires_at_ts": expires_at_ts,
        "loaded_models": [
            {
                "admitted": True,
                "expires_at_ts": expires_at_ts,
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


def approved_paths(*, expires_at_ts: int = 1_060_000):
    return [
        {
            "runtime_instance_id": "lmstudio-xwing-1234",
            "base_url": "http://192.168.1.9:1234/v1",
            "transport": "lan",
            "preference": 10,
            "expires_at_ts": expires_at_ts,
            "approved_by": "operator",
            "approval_id": "approval-path-lan",
        },
        {
            "runtime_instance_id": "lmstudio-xwing-1234",
            "base_url": "http://100.64.0.9:1234/v1",
            "transport": "tailscale",
            "preference": 20,
            "expires_at_ts": expires_at_ts,
            "approved_by": "operator",
            "approval_id": "approval-path-tailnet",
        },
    ]


def approved_capacity(*, expires_at_ts: int = 1_060_000):
    return [
        {
            "runtime_instance_id": "lmstudio-xwing-1234",
            "parallel_slots": 1,
            "queue_limit": 4,
            "queue_timeout_seconds": 30,
            "expires_at_ts": expires_at_ts,
            "approved_by": "operator",
            "approval_id": "approval-capacity",
        }
    ]


def install_fixtures(
    monkeypatch,
    runtime=None,
    paths=None,
    capacity=None,
    *,
    state_expiry: int = 1_060_000,
):
    monkeypatch.setattr(
        runtime_projection,
        "_projection_state",
        lambda _factory, _now: {
            "generation": 7,
            "revision": "fleet-7",
            "status": "approved",
            "approved_by": "operator",
            "approval_id": "approval-generation",
            "manifest_checksum": "a" * 64,
            "expires_at_ts": state_expiry,
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


def test_projection_contains_resolved_identity_and_bounded_signed_lease(monkeypatch):
    install_fixtures(monkeypatch)

    document = runtime_projection.build_runtime_projection(
        lambda: None,
        secret=SECRET,
        ttl_seconds=120,
        now_ms=1_000_000,
    )

    assert document["generation"] == 7
    assert document["revision"] == "fleet-7"
    assert document["generated_at_ms"] == 1_000_000
    # The 120-second requested lease is bounded by the 60-second evidence expiry.
    assert document["expires_at_ms"] == 1_060_000
    assert document["checksum"] == runtime_projection.projection_checksum(document)
    assert document["signature"] == runtime_projection.projection_signature(
        7,
        document["checksum"],
        document["generated_at_ms"],
        document["expires_at_ms"],
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


def test_same_generation_refresh_has_stable_config_checksum_and_new_signature(
    monkeypatch,
):
    install_fixtures(
        monkeypatch,
        runtime=complete_runtime(expires_at_ts=1_120_000),
        paths=approved_paths(expires_at_ts=1_120_000),
        capacity=approved_capacity(expires_at_ts=1_120_000),
        state_expiry=1_120_000,
    )

    first = runtime_projection.build_runtime_projection(
        lambda: None,
        secret=SECRET,
        ttl_seconds=60,
        now_ms=1_000_000,
    )
    second = runtime_projection.build_runtime_projection(
        lambda: None,
        secret=SECRET,
        ttl_seconds=60,
        now_ms=1_030_000,
    )

    assert first["checksum"] == second["checksum"]
    assert first["generated_at_ms"] != second["generated_at_ms"]
    assert first["expires_at_ms"] != second["expires_at_ms"]
    assert first["signature"] != second["signature"]


def test_projection_fails_closed_without_capacity_model_or_fresh_evidence(
    monkeypatch,
):
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

    install_fixtures(monkeypatch, state_expiry=999_999)
    with pytest.raises(
        runtime_projection.RuntimeProjectionBlocked,
        match="approval evidence is expired",
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
        match="ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET is required",
    ):
        runtime_projection.build_runtime_projection(
            lambda: None,
            secret="",
            now_ms=1_000_000,
        )
