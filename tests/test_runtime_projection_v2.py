from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx import runtime_projection_v2


def _decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def test_projection_v2_signs_router_compatible_document(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    legacy_document = {
        "schema_version": "1",
        "source": "assistx",
        "generation": 4,
        "revision": "fleet-4",
        "generated_at_ms": 1_000_000,
        "expires_at_ms": 1_060_000,
        "providers": [
            {
                "name": "assistx-xwing-runtime",
                "type": "lmstudio",
                "node_id": "xwing",
                "runtime_instance_id": "runtime-xwing",
                "runtime_kind": "lmstudio",
                "runtime_version": "0.4.7",
                "parallel_slots": 1,
                "queue_limit": 4,
                "queue_timeout_seconds": 30,
                "enabled": True,
                "base_url": "http://127.0.0.1:1234/v1",
                "access_urls": ["http://127.0.0.1:1234/v1"],
                "priority": 100,
                "quota_class": "local",
                "models": [
                    {
                        "alias": "local/qwen",
                        "provider_model": "qwen.gguf",
                        "model_instance_id": "model-xwing",
                        "artifact_fingerprint": "sha256:" + "a" * 64,
                        "quantization": "Q4_K_M",
                        "capabilities": ["chat", "local_only"],
                        "context_window": 32768,
                    }
                ],
            }
        ],
        "checksum": "ignored",
        "signature": "ignored",
    }
    monkeypatch.setattr(
        runtime_projection_v2.legacy,
        "build_runtime_projection",
        lambda *_args, **_kwargs: dict(legacy_document),
    )

    document = runtime_projection_v2.build_runtime_projection_v2(
        lambda: None,
        private_key=private_key,
        key_id="projection-key-1",
    )

    assert document["schema_version"] == "2"
    assert document["signature_algorithm"] == "Ed25519"
    assert document["signature_key_id"] == "projection-key-1"
    assert document["checksum"] == (
        runtime_projection_v2.legacy.projection_checksum(document)
    )
    private_key.public_key().verify(
        _decode(document["signature"]),
        runtime_projection_v2.signing_message(document),
    )


def test_projection_signature_covers_generation_and_expiry(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        runtime_projection_v2.legacy,
        "build_runtime_projection",
        lambda *_args, **_kwargs: {
            "schema_version": "1",
            "source": "assistx",
            "generation": 1,
            "revision": "fleet-1",
            "generated_at_ms": 1_000_000,
            "expires_at_ms": 1_060_000,
            "providers": [{"name": "local"}],
            "checksum": "ignored",
            "signature": "ignored",
        },
    )
    document = runtime_projection_v2.build_runtime_projection_v2(
        lambda: None,
        private_key=private_key,
    )
    signature = _decode(document["signature"])
    tampered = dict(document)
    tampered["expires_at_ms"] += 1

    try:
        private_key.public_key().verify(
            signature,
            runtime_projection_v2.signing_message(tampered),
        )
    except Exception:
        pass
    else:
        raise AssertionError("tampered expiry unexpectedly verified")
