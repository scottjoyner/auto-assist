from __future__ import annotations

import copy

import pytest

from assistx.runtime_admission import (
    RuntimeAdmissionContractError,
    build_runtime_admission_candidate,
    validate_runtime_admission_candidate,
)


LOADOUT = "sha256:" + "a" * 64
MODEL = "sha256:" + "b" * 64


def profile() -> dict:
    return {
        "schema_version": "fleet-runtime-profile.canary.v1",
        "profile_id": "x1-370-qwen-primary",
        "revision": 1,
        "node": {"node_id": "x1-370"},
        "admission": {"enabled": False},
        "desired": {
            "runtime": {
                "physical_instance": "llama.cpp:x1-370:1234",
                "kind": "llama_cpp",
                "backend": "vulkan",
            },
            "model": {
                "id": "qwen-primary",
                "artifact_fingerprint": MODEL,
                "quantization": "Q4_K_M",
            },
            "capacity": {
                "parallel_slots": 1,
                "max_context_tokens": 32768,
            },
            "loadout": {
                "loadout_fingerprint": LOADOUT,
                "context": {"configured_tokens": 32768},
            },
        },
        "rollback": {
            "profile_id": "lmstudio-stable",
            "procedure": ["stop candidate", "restore stable runtime"],
        },
        "evidence": {
            "bundle_fingerprint": "sha256:" + "c" * 64,
            "qualification_fingerprint": "sha256:" + "d" * 64,
            "qualification_run_attestation_fingerprint": (
                "sha256:" + "e" * 64
            ),
            "qualification": {
                "loadout_fingerprint": LOADOUT,
                "fingerprint": "sha256:" + "f" * 64,
            },
            "runtime_canary": {
                "success": True,
                "rollback_succeeded": True,
                "non_admitting": True,
                "loadout_fingerprint": LOADOUT,
                "manifest_fingerprint": "sha256:" + "1" * 64,
                "attestation_fingerprint": "sha256:" + "2" * 64,
                "signer_identity": "runtime-canary-prod",
                "signing_key_fingerprint": "SHA256:example",
            },
            "gates": {
                "exact_loadout_qualification": True,
                "hermes_intelligence": True,
                "hermes_context_pressure": True,
                "authenticated_qualification_run": True,
                "runtime_canary_soak": True,
                "runtime_canary_rollback": True,
                "runtime_canary_attested": True,
            },
        },
    }


def live_proof() -> dict:
    return {
        "schema_version": "assistx.live-runtime-proof.v1",
        "observed_at_ms": 1_000_000,
        "expires_at_ms": 1_600_000,
        "node_id": "x1-370",
        "runtime_instance_id": "llama.cpp:x1-370:1234",
        "runtime_kind": "llama_cpp",
        "runtime_version": "b7000",
        "headless": True,
        "process_id": "4242",
        "model_instance_id": "qwen-primary:x1-370",
        "model_key": "local/qwen-primary",
        "provider_model": "qwen-primary.gguf",
        "artifact_fingerprint": MODEL,
        "quantization": "Q4_K_M",
        "context_length": 32768,
        "capabilities": ["chat", "streaming", "tool_use"],
        "capacity": {
            "parallel_slots": 1,
            "queue_limit": 4,
            "queue_timeout_seconds": 30,
            "shared_capacity_key": "llama.cpp:x1-370:1234",
        },
        "access_paths": [
            {
                "base_url": "http://192.168.1.50:1234/v1",
                "transport": "lan",
                "preference": 10,
            },
            {
                "base_url": "http://100.64.0.50:1234/v1",
                "transport": "tailscale",
                "preference": 20,
            },
        ],
        "rollback": {
            "profile_id": "lmstudio-stable",
            "health_verified": True,
            "boot_recovery_verified": True,
        },
    }


def test_builds_expiring_non_self_authorizing_candidate():
    candidate = build_runtime_admission_candidate(
        profile(),
        live_proof(),
        generation=7,
        expected_current_generation=6,
        approved_by="operator",
        approval_id="approval-7",
        ttl_seconds=300,
        now_ms=1_100_000,
    )

    assert candidate["profile"]["admission_enabled"] is False
    assert candidate["lease"]["state"] == "ACTIVE"
    assert candidate["lease"]["expires_at_ms"] == 1_400_000
    assert candidate["projection_manifest"]["generation"] == 7
    assert candidate["runtime"]["access_paths"][0]["transport"] == "lan"
    assert validate_runtime_admission_candidate(
        candidate,
        now_ms=1_100_000,
    ) is candidate


def test_rejects_profile_that_attempts_to_self_admit():
    attempted = profile()
    attempted["admission"]["enabled"] = True

    with pytest.raises(
        RuntimeAdmissionContractError,
        match="admission.enabled must be false",
    ):
        build_runtime_admission_candidate(
            attempted,
            live_proof(),
            generation=1,
            expected_current_generation=0,
            approved_by="operator",
            approval_id="approval-1",
            now_ms=1_100_000,
        )


def test_rejects_tampered_candidate_and_capacity_aliasing():
    candidate = build_runtime_admission_candidate(
        profile(),
        live_proof(),
        generation=1,
        expected_current_generation=0,
        approved_by="operator",
        approval_id="approval-1",
        now_ms=1_100_000,
    )
    tampered = copy.deepcopy(candidate)
    tampered["runtime"]["capacity"]["parallel_slots"] = 2

    with pytest.raises(
        RuntimeAdmissionContractError,
        match="candidate fingerprint mismatch",
    ):
        validate_runtime_admission_candidate(tampered, now_ms=1_100_000)

    inconsistent = live_proof()
    inconsistent["capacity"]["parallel_slots"] = 2
    with pytest.raises(
        RuntimeAdmissionContractError,
        match="parallel slot capacity does not match",
    ):
        build_runtime_admission_candidate(
            profile(),
            inconsistent,
            generation=1,
            expected_current_generation=0,
            approved_by="operator",
            approval_id="approval-1",
            now_ms=1_100_000,
        )
