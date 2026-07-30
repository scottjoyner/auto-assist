from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "approve_runtime_projection"
SPEC = importlib.util.spec_from_file_location(
    MODULE_NAME,
    ROOT / "scripts" / "approve-runtime-projection.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = module
SPEC.loader.exec_module(module)


def valid_manifest() -> dict:
    return {
        "schema_version": 1,
        "generation": 3,
        "expected_current_generation": 2,
        "revision": "fleet-generation-3",
        "approved_by": "operator",
        "approval_id": "change-123",
        "ttl_seconds": 300,
        "require_lan_and_tailscale": True,
        "runtimes": [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "runtime_kind": "lmstudio",
                "runtime_version": "0.4.7",
                "headless": False,
                "process_id": "4242",
                "capacity": {
                    "parallel_slots": 1,
                    "queue_limit": 4,
                    "queue_timeout_seconds": 30,
                    "evidence_ref": "artifacts/xwing-capacity.json",
                },
                "access_paths": [
                    {
                        "base_url": "http://192.168.1.9:1234/v1",
                        "transport": "lan",
                        "preference": 10,
                        "evidence_ref": "artifacts/xwing-lan.json",
                    },
                    {
                        "base_url": "http://100.64.0.9:1234/v1",
                        "transport": "tailscale",
                        "preference": 20,
                        "evidence_ref": "artifacts/xwing-tailnet.json",
                    },
                ],
                "models": [
                    {
                        "model_instance_id": "model-xwing-1",
                        "model_key": "local/qwen-code",
                        "provider_model": "qwen.gguf",
                        "artifact_fingerprint": "sha256:abcdef",
                        "quantization": "Q4_K_M",
                        "context_length": 32768,
                        "capabilities": ["chat", "code", "local_only"],
                        "evidence_ref": "artifacts/xwing-model.json",
                    }
                ],
            }
        ],
    }


def test_valid_manifest_is_checksum_stable_and_dry_run_safe() -> None:
    first = module.validate_manifest(valid_manifest())
    second = module.validate_manifest(valid_manifest())

    assert first.checksum == second.checksum
    assert first.payload["generation"] == 3
    assert first.payload["runtimes"][0]["capacity"]["parallel_slots"] == 1


def test_manifest_rejects_generation_skip_public_path_and_unknown_artifact() -> None:
    payload = valid_manifest()
    payload["generation"] = 5
    payload["runtimes"][0]["access_paths"][0]["base_url"] = (
        "https://api.example.com/v1"
    )
    payload["runtimes"][0]["models"][0]["artifact_fingerprint"] = "unknown"

    with pytest.raises(ValueError) as exc:
        module.validate_manifest(payload)

    message = str(exc.value)
    assert "exactly expected_current_generation + 1" in message
    assert "private and valid" in message
    assert "artifact_fingerprint must be resolved" in message


def test_manifest_requires_lan_first_and_tailscale_fallback() -> None:
    payload = valid_manifest()
    payload["runtimes"][0]["access_paths"] = [
        {
            "base_url": "http://100.64.0.9:1234/v1",
            "transport": "tailscale",
            "preference": 10,
            "evidence_ref": "artifacts/xwing-tailnet.json",
        }
    ]

    with pytest.raises(ValueError) as exc:
        module.validate_manifest(payload)

    message = str(exc.value)
    assert "both LAN and Tailscale" in message
    assert "prefer LAN" in message
