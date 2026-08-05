from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "build_runtime_projection_candidate"
SPEC = importlib.util.spec_from_file_location(
    MODULE_NAME,
    ROOT / "scripts" / "build-runtime-projection-candidate.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = module
SPEC.loader.exec_module(module)

APPROVAL_MODULE_NAME = "approve_runtime_projection"
APPROVAL_SPEC = importlib.util.spec_from_file_location(
    APPROVAL_MODULE_NAME,
    ROOT / "scripts" / "approve-runtime-projection.py",
)
assert APPROVAL_SPEC and APPROVAL_SPEC.loader
approval_module = importlib.util.module_from_spec(APPROVAL_SPEC)
sys.modules[APPROVAL_MODULE_NAME] = approval_module
APPROVAL_SPEC.loader.exec_module(approval_module)


def valid_profile() -> dict:
    return {
        "schema_version": "fleet_runtime_profile.v2",
        "profile_id": "x1-370.llama-vulkan.canary",
        "revision": 4,
        "node": {"node_id": "x1-370"},
        "observation": {
            "artifact": "observations/x1-370/machine_observation.json",
            "expires_at_utc": "2026-08-07T00:00:00Z",
        },
        "desired": {
            "runtime": {
                "kind": "llama.cpp",
                "physical_instance": "llama.cpp:x1-370:1234",
                "engine_version": "b6000",
                "headless": True,
                "access_paths": [
                    {
                        "transport": "lan",
                        "url": "http://192.168.1.9:1234/v1",
                        "priority": 10,
                    },
                    {
                        "transport": "tailscale",
                        "url": "http://100.64.0.9:1234/v1",
                        "priority": 20,
                    },
                ],
            },
            "model": {
                "id": "local/qwen-code",
                "artifact_fingerprint": "sha256:" + "2" * 64,
                "quantization": "Q4_K_M",
            },
            "capacity": {
                "parallel_slots": 1,
                "max_context_tokens": 32768,
            },
        },
        "evidence": {
            "runtime_canary": {
                "manifest_fingerprint": "sha256:" + "3" * 64,
                "attestation_fingerprint": "sha256:" + "4" * 64,
                "signer_identity": "operator@example",
                "signing_key_fingerprint": "SHA256:examplekey",
                "success": True,
                "rollback_succeeded": True,
                "soak": {"passed": True},
                "admission": {"admitted": False},
            },
            "gates": {
                "runtime_canary_soak": True,
                "runtime_canary_rollback": True,
                "runtime_canary_attested": True,
            },
            "artifacts": {
                "reliability": "evidence/x1-370/reliability.json",
                "model_inventory": "evidence/x1-370/model_inventory.json",
            },
        },
        "admission": {
            "enabled": False,
            "required_external_gates": [
                "physical_runtime_identity",
                "model_artifact_fingerprint",
                "container_path_reachability",
                "lan_preference_and_tailscale_fallback",
                "shared_slot_admission",
                "rollback_canary",
            ],
        },
    }


def build(profile: dict) -> dict:
    return module.build_candidate(
        profile,
        generation=3,
        expected_current_generation=2,
        approved_by="operator",
        approval_id="change-123",
        process_id="4242",
        now=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


def test_builds_deterministic_non_mutating_projection_candidate() -> None:
    first = build(valid_profile())
    second = build(valid_profile())

    assert first == second
    assert first["generation"] == 3
    assert first["expected_current_generation"] == 2
    assert first["source_profile"]["non_admitting_source"] is True
    assert first["source_profile"]["fingerprint"].startswith("sha256:")
    runtime = first["runtimes"][0]
    assert runtime["runtime_kind"] == "llama_cpp"
    assert runtime["runtime_version"] == "b6000"
    assert runtime["process_id"] == "4242"
    assert [item["transport"] for item in runtime["access_paths"]] == [
        "lan",
        "tailscale",
    ]
    model = runtime["models"][0]
    assert model["model_key"] == "local/qwen-code"
    assert model["context_length"] == 32768
    assert "local_only" in model["capabilities"]
    validated = approval_module.validate_manifest(first)
    assert validated.payload == first
    assert len(validated.checksum) == 64


def test_rejects_profile_that_is_already_admitting() -> None:
    profile = valid_profile()
    profile["admission"]["enabled"] = True

    with pytest.raises(module.ProfileCandidateError, match="non-admitting"):
        build(profile)


def test_rejects_failed_canary_or_rollback() -> None:
    profile = valid_profile()
    profile["evidence"]["runtime_canary"]["rollback_succeeded"] = False

    with pytest.raises(module.ProfileCandidateError, match="rollback"):
        build(profile)


def test_rejects_expired_observation() -> None:
    profile = valid_profile()
    profile["observation"]["expires_at_utc"] = "2026-08-04T00:00:00Z"

    with pytest.raises(module.ProfileCandidateError, match="expired"):
        build(profile)


def test_requires_exact_generation_step_and_dual_private_paths() -> None:
    profile = valid_profile()
    profile["desired"]["runtime"]["access_paths"] = [
        {
            "transport": "tailscale",
            "url": "http://100.64.0.9:1234/v1",
            "priority": 20,
        }
    ]

    with pytest.raises(module.ProfileCandidateError, match="both LAN and Tailscale"):
        build(profile)

    with pytest.raises(module.ProfileCandidateError, match="generation"):
        module.build_candidate(
            valid_profile(),
            generation=5,
            expected_current_generation=2,
            approved_by="operator",
            approval_id="change-123",
            process_id="4242",
            now=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
