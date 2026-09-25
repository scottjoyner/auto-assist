from __future__ import annotations

import pytest

from assistx.runtime_telemetry_observer import (
    join_runtime_snapshots,
    validate_runtime_snapshot,
)


def _policy(**overrides):
    value = {
        "policy_id": "r9700-q4-dflash",
        "node_id": "r9700",
        "model_handle": "qwen3.8-27b-q4",
        "backend": "rocm",
        "quantization": "q4",
        "speculation": "dflash",
        "telemetry_required": True,
        "telemetry_env": "BENCH_TELEMETRY_URL",
    }
    value.update(overrides)
    return value


def _snapshot(
    *,
    observed_at=1000,
    counters=None,
    gauges=None,
    **identity_overrides,
):
    identity = {
        "node_id": "r9700",
        "model_handle": "qwen3.8-27b-q4",
        "backend": "rocm",
        "quantization": "q4",
        "speculation": "dflash",
        "runtime_revision": "llama.cpp@abc123",
        "launch_config_sha256": "a" * 64,
        "process_started_at_unix_ms": 900,
    }
    identity.update(identity_overrides)
    return {
        "schema": "assistx-runtime-telemetry-snapshot-v1",
        "scope": "process",
        "trial_id": "trial-1",
        "observed_at_unix_ms": observed_at,
        "identity": identity,
        "counters": counters or {},
        "gauges": gauges or {},
        "source": {"read_only": True},
    }


def _capture(snapshot, policy=None):
    normalized = validate_runtime_snapshot(
        snapshot,
        policy or _policy(),
        trial_id="trial-1",
    )
    return {
        "configured": True,
        "valid": True,
        "snapshot": normalized,
        "snapshot_sha256": normalized["snapshot_sha256"],
    }


def test_snapshot_rejects_declared_policy_mismatch():
    with pytest.raises(ValueError, match="runtime identity mismatch"):
        validate_runtime_snapshot(
            _snapshot(speculation="mtp"),
            _policy(),
            trial_id="trial-1",
        )


def test_snapshot_binds_expected_revision_and_launch_hash(monkeypatch):
    monkeypatch.setenv("EXPECTED_REV", "llama.cpp@abc123")
    monkeypatch.setenv("EXPECTED_LAUNCH", "a" * 64)
    policy = _policy(
        expected_runtime_revision_env="EXPECTED_REV",
        expected_launch_config_sha256_env="EXPECTED_LAUNCH",
    )

    value = validate_runtime_snapshot(
        _snapshot(),
        policy,
        trial_id="trial-1",
    )

    assert value["identity"]["runtime_revision"] == "llama.cpp@abc123"
    assert value["identity"]["launch_config_sha256"] == "a" * 64


def test_snapshot_fails_closed_on_wrong_expected_launch_hash(monkeypatch):
    monkeypatch.setenv("EXPECTED_LAUNCH", "b" * 64)
    with pytest.raises(ValueError, match="mismatch"):
        validate_runtime_snapshot(
            _snapshot(),
            _policy(
                expected_launch_config_sha256_env="EXPECTED_LAUNCH",
            ),
            trial_id="trial-1",
        )


def test_join_derives_speculation_and_throughput_metrics():
    before = _capture(
        _snapshot(
            observed_at=1000,
            counters={
                "prefill_tokens": 100,
                "prefill_seconds": 2,
                "decode_tokens": 50,
                "decode_seconds": 5,
                "spec_proposed_tokens": 20,
                "spec_accepted_tokens": 10,
                "spec_verification_steps": 4,
                "cache_reused_tokens": 25,
                "energy_joules": 100,
            },
            gauges={
                "vram_bytes": 8_000,
                "power_watts": 100,
            },
        )
    )
    after = _capture(
        _snapshot(
            observed_at=2000,
            counters={
                "prefill_tokens": 300,
                "prefill_seconds": 4,
                "decode_tokens": 150,
                "decode_seconds": 9,
                "spec_proposed_tokens": 60,
                "spec_accepted_tokens": 42,
                "spec_verification_steps": 12,
                "cache_reused_tokens": 125,
                "energy_joules": 180,
            },
            gauges={
                "vram_bytes": 9_000,
                "power_watts": 125,
            },
        )
    )

    joined = join_runtime_snapshots(
        before,
        after,
        _policy(),
        trial_id="trial-1",
    )

    assert joined["valid"] is True
    assert joined["counter_deltas"]["spec_proposed_tokens"] == 40
    assert joined["counter_deltas"]["spec_accepted_tokens"] == 32
    assert joined["derived"]["spec_acceptance_rate"] == 0.8
    assert joined["derived"]["prefill_tokens_per_second"] == 100.0
    assert joined["derived"]["decode_tokens_per_second"] == 25.0
    assert joined["derived"]["joules_per_decode_token"] == 0.8
    assert joined["derived"]["cache_reuse_rate"] == 0.5
    assert joined["gauge_samples"]["vram_bytes"]["after"] == 9000


def test_join_rejects_runtime_restart_or_launch_change():
    before = _capture(_snapshot())
    after = _capture(
        _snapshot(
            observed_at=2000,
            process_started_at_unix_ms=1500,
            launch_config_sha256="b" * 64,
        )
    )

    joined = join_runtime_snapshots(
        before,
        after,
        _policy(),
        trial_id="trial-1",
    )

    assert joined["valid"] is False
    assert joined["reason"] == "runtime_identity_changed"


def test_join_rejects_counter_regression():
    before = _capture(
        _snapshot(counters={"decode_tokens": 100})
    )
    after = _capture(
        _snapshot(observed_at=2000, counters={"decode_tokens": 50})
    )

    joined = join_runtime_snapshots(
        before,
        after,
        _policy(),
        trial_id="trial-1",
    )

    assert joined["valid"] is False
    assert joined["reason"] == "counter_regression"


def test_process_scope_detects_concurrent_request_contamination():
    before = _capture(
        _snapshot(counters={"requests_total": 10})
    )
    after = _capture(
        _snapshot(
            observed_at=2000,
            counters={"requests_total": 12},
        )
    )

    joined = join_runtime_snapshots(
        before,
        after,
        _policy(),
        trial_id="trial-1",
    )

    assert joined["valid"] is False
    assert joined["reason"] == "process_scope_request_delta_not_one"
    assert joined["routing_authority_changed"] is False
