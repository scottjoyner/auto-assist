#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _add_source(path: str) -> None:
    resolved = str(Path(path).resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assistx-src", required=True)
    parser.add_argument("--router-src", required=True)
    parser.add_argument("--matrix-out", required=True)
    parser.add_argument("--assistx-sha", required=True)
    parser.add_argument("--router-sha", required=True)
    parser.add_argument("--lms-sha", required=True)
    parser.add_argument("--profiles-sha", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _add_source(args.router_src)
    _add_source(args.assistx_src)

    from assistx import runtime_projection_v2 as producer
    from auto_router import runtime_projection_v2 as consumer

    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    os.environ["AUTO_ROUTER_STRICT_OFFLINE"] = "true"
    os.environ["AUTO_ROUTER_RUNTIME_PROJECTION_KEY_ID"] = "cross-repo-test"
    os.environ["AUTO_ROUTER_RUNTIME_PROJECTION_VERIFY_KEY_PEM"] = public_pem

    legacy_document = {
        "schema_version": "1",
        "source": "assistx",
        "generation": 9,
        "revision": "cross-repo-9",
        "generated_at_ms": 1_000_000,
        "expires_at_ms": 1_060_000,
        "providers": [
            {
                "name": "assistx-x1-370",
                "type": "llama_cpp",
                "node_id": "x1-370",
                "runtime_instance_id": "llama.cpp:x1-370:1234",
                "runtime_kind": "llama_cpp",
                "runtime_version": "b7000",
                "headless": True,
                "parallel_slots": 1,
                "queue_limit": 4,
                "queue_timeout_seconds": 30,
                "enabled": True,
                "base_url": "http://192.168.1.50:1234/v1",
                "access_urls": [
                    "http://192.168.1.50:1234/v1",
                    "http://100.64.0.50:1234/v1",
                ],
                "priority": 100,
                "quota_class": "local",
                "models": [
                    {
                        "alias": "local/qwen-primary",
                        "provider_model": "qwen-primary.gguf",
                        "model_instance_id": "qwen-primary:x1-370",
                        "artifact_fingerprint": "sha256:" + "b" * 64,
                        "quantization": "Q4_K_M",
                        "capabilities": ["chat", "streaming", "local_only"],
                        "context_window": 32768,
                    }
                ],
            }
        ],
        "checksum": "ignored",
        "signature": "ignored",
    }
    producer.legacy.build_runtime_projection = (
        lambda *_args, **_kwargs: dict(legacy_document)
    )
    document = producer.build_runtime_projection_v2(
        lambda: None,
        private_key=private_key,
        key_id="cross-repo-test",
    )
    parsed, _converted = consumer.validate_projection_document(
        document,
        now_ms=1_010_000,
    )
    assert parsed.generation == 9
    assert parsed.providers[0].runtime_instance_id == "llama.cpp:x1-370:1234"

    tampered = json.loads(json.dumps(document))
    tampered["providers"][0]["parallel_slots"] = 2
    try:
        consumer.validate_projection_document(tampered, now_ms=1_010_000)
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("tampered provider capacity was accepted")

    expiry_tampered = json.loads(json.dumps(document))
    expiry_tampered["expires_at_ms"] += 1
    try:
        consumer.validate_projection_document(expiry_tampered, now_ms=1_010_000)
    except ValueError as exc:
        assert "signature mismatch" in str(exc)
    else:
        raise AssertionError("tampered projection expiry was accepted")

    signature_bytes = base64.urlsafe_b64decode(
        document["signature"] + "=" * ((4 - len(document["signature"]) % 4) % 4)
    )
    private_key.public_key().verify(
        signature_bytes,
        producer.signing_message(document),
    )

    matrix = {
        "schema_version": "assistx.cross-repository-contract.v1",
        "assistx": args.assistx_sha,
        "auto_router": args.router_sha,
        "lms": args.lms_sha,
        "fleet_llm_profiles": args.profiles_sha,
        "checks": {
            "assistx_projection_generated": True,
            "auto_router_projection_accepted": True,
            "capacity_tamper_rejected": True,
            "expiry_tamper_rejected": True,
        },
    }
    output = Path(args.matrix_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    print(json.dumps(matrix, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
