from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx import runtime_projection as legacy
from assistx import runtime_projection_v2


def _install_fixtures(monkeypatch) -> None:
    # Independent signed-projection fixtures must explicitly supply routing
    # policy evidence. Do not query a live graph or silently bypass policy.
    monkeypatch.setattr(
        runtime_projection_v2, "node_routing_policy_index",
        lambda _factory: {},
    )
    monkeypatch.setattr(
        runtime_projection_v2, "benchmark_projection_index",
        lambda _factory: {},
    )
    monkeypatch.setattr(
        legacy,
        "_projection_state",
        lambda _factory, _now: {
            "generation": 11,
            "revision": "fleet-11",
            "status": "approved",
            "approved_by": "operator",
            "approval_id": "projection-approval",
            "manifest_checksum": "a" * 64,
            "expires_at_ts": 1_060_000,
        },
    )
    monkeypatch.setattr(
        legacy,
        "_runtime_rows",
        lambda _factory, _now: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "runtime_kind": "lmstudio",
                "runtime_version": "0.4.7",
                "headless": False,
                "expires_at_ts": 1_060_000,
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
                        "capabilities_json": ["chat", "streaming", "local_only"],
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        legacy,
        "_access_rows",
        lambda _factory, _now: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "base_url": "http://192.168.1.9:1234/v1",
                "transport": "lan",
                "preference": 10,
                "expires_at_ts": 1_060_000,
                "approved_by": "operator",
                "approval_id": "path-approval",
            }
        ],
    )
    monkeypatch.setattr(
        legacy,
        "_capacity_rows",
        lambda _factory, _now: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "parallel_slots": 1,
                "queue_limit": 4,
                "queue_timeout_seconds": 30,
                "expires_at_ts": 1_060_000,
                "approved_by": "operator",
                "approval_id": "capacity-approval",
            }
        ],
    )


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def test_projection_v2_is_signed_with_ed25519(monkeypatch):
    _install_fixtures(monkeypatch)
    private_key = Ed25519PrivateKey.generate()

    document = runtime_projection_v2.build_runtime_projection(
        lambda: None,
        private_key=private_key,
        key_id="projection-key-2026",
        ttl_seconds=60,
        now_ms=1_000_000,
    )

    assert document["schema_version"] == "2"
    assert document["signature_algorithm"] == "Ed25519"
    assert document["signature_key_id"] == "projection-key-2026"
    assert document["checksum"] == legacy.projection_checksum(document)
    private_key.public_key().verify(
        _decode(document["signature"]),
        runtime_projection_v2.signing_message(document),
    )


def test_signature_binds_generation_checksum_timestamps_and_key_id(monkeypatch):
    _install_fixtures(monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    document = runtime_projection_v2.build_runtime_projection(
        lambda: None,
        private_key=private_key,
        key_id="projection-key-2026",
        now_ms=1_000_000,
    )

    for field, value in (
        ("generation", 12),
        ("expires_at_ms", 1_059_999),
        ("signature_key_id", "other-key"),
    ):
        tampered = dict(document)
        tampered[field] = value
        with pytest.raises(Exception):
            private_key.public_key().verify(
                _decode(document["signature"]),
                runtime_projection_v2.signing_message(tampered),
            )


def test_private_key_file_requires_strict_permissions(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    raw = private_key.private_bytes_raw()
    path = tmp_path / "projection.key"
    path.write_text(base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="))
    path.chmod(0o644)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_FILE", str(path))

    with pytest.raises(
        legacy.RuntimeProjectionBlocked,
        match="permissions must be 0600",
    ):
        runtime_projection_v2.load_private_key()

    path.chmod(0o600)
    assert isinstance(runtime_projection_v2.load_private_key(), Ed25519PrivateKey)


def test_signed_projection_missing_role_metadata_does_not_grant_execution(monkeypatch):
    _install_fixtures(monkeypatch)
    document = runtime_projection_v2.build_runtime_projection(
        lambda: None, private_key=Ed25519PrivateKey.generate(),
        now_ms=1_000_000,
    )
    assert len(document["providers"]) == 1
    provider = document["providers"][0]
    assert provider["worker_mode"] == "observer_only"
    assert provider["routing_roles"] == []
    assert provider["allow_agent_runtime"] is False
    assert provider["allow_code_execution"] is False
    assert len(provider["models"]) == 1
    model = provider["models"][0]
    assert model["task_family_scores"] == {}
    assert model["allow_agent_runtime"] is False
    assert model["allow_code_execution"] is False


def test_signed_projection_routing_reader_failure_blocks_issuance(monkeypatch):
    _install_fixtures(monkeypatch)

    def read_denied(_factory):
        raise PermissionError("Synthetic restricted reader")

    monkeypatch.setattr(runtime_projection_v2, "node_routing_policy_index", read_denied)
    with pytest.raises(legacy.RuntimeProjectionBlocked, match="metadata evidence unavailable"):
        runtime_projection_v2.build_runtime_projection(
            lambda: None, private_key=Ed25519PrivateKey.generate(),
            now_ms=1_000_000,
        )


def test_metadata_cannot_admit_unknown_models_or_grant_privileges(monkeypatch):
    _install_fixtures(monkeypatch)
    monkeypatch.setattr(
        runtime_projection_v2, "node_routing_policy_index",
        lambda _factory: {
            "xwing": {
                "worker_mode": "full_agent",
                "routing_roles": ["coding"],
                "allow_agent_runtime": True,
                "allow_code_execution": True,
            }
        },
    )
    monkeypatch.setattr(
        runtime_projection_v2, "benchmark_projection_index",
        lambda _factory: {
            ("xwing", "local/qwen"): {
                "task_family_scores": {"coding": {
                    "quality_score": 0.9, "quality_floor_passed": True
                }},
                "allow_code_execution": True,
            },
            ("xwing", "non-admitted-model"): {
                "task_family_scores": {"coding": {
                    "quality_score": 1.0, "quality_floor_passed": True
                }}
            },
        },
    )
    key = Ed25519PrivateKey.generate()
    document = runtime_projection_v2.build_runtime_projection(
        lambda: None, private_key=key, now_ms=1_000_000,
    )
    provider = document["providers"][0]
    assert provider["worker_mode"] == "full_agent"
    assert provider["allow_code_execution"] is False
    assert provider["allow_agent_runtime"] is False
    assert len(provider["models"]) == 1
    model = provider["models"][0]
    assert model["alias"] == "local/qwen"
    assert model["task_family_scores"]["coding"]["quality_score"] == 0.9
    assert model["allow_code_execution"] is False
    assert model["allow_agent_runtime"] is False
    key.public_key().verify(
        _decode(document["signature"]),
        runtime_projection_v2.signing_message(document),
    )
