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
    from auto_router.benchmark_routing_policy import benchmark_order
    from auto_router.models import (
        ExecutionStage,
        ProviderCandidate,
        RouterRequest,
        StagePurpose,
    )

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
    # The legacy authoritative projection is an isolated deterministic
    # fixture. Stub only read-only graph evidence indices; the signed v2
    # producer and actual router verifier still execute end-to-end.
    producer.node_routing_policy_index = lambda _factory: {
        "x1-370": {
            "routing_roles": ["summarization"],
            "worker_mode": "auxiliary",
            "allow_agent_runtime": False,
            "allow_code_execution": False,
        },
    }
    producer.benchmark_projection_index = lambda _factory: {
        ("x1-370", "local/qwen-primary"): {
            "task_family_scores": {
                "summarization": {
                    "quality_floor_passed": True,
                    "utility_score": 0.8,
                }
            }
        },
        ("x1-370", "unadmitted-model"): {
            "task_family_scores": {"coding": {"utility_score": 1.0}}
        },
    }
    document = producer.build_runtime_projection(
        lambda: None,
        private_key=private_key,
        key_id="cross-repo-test",
    )
    assert len(document["providers"]) == 1
    provider = document["providers"][0]
    assert provider["worker_mode"] == "auxiliary"
    assert provider["allow_code_execution"] is False
    assert len(provider["models"]) == 1
    assert provider["models"][0]["alias"] == "local/qwen-primary"
    assert "coding" not in provider["models"][0]["task_family_scores"]
    parsed, _converted = consumer.validate_projection_document(
        document,
        now_ms=1_010_000,
    )
    assert parsed.generation == 9
    assert parsed.providers[0].runtime_instance_id == "llama.cpp:x1-370:1234"
    # Verify actual consumer schema retention, not merely signature acceptance.
    assert parsed.providers[0].worker_mode == "auxiliary"
    assert parsed.providers[0].routing_roles == {"summarization"}
    assert parsed.providers[0].allow_code_execution is False
    parsed_model = parsed.providers[0].models[0]
    assert parsed_model.alias == "local/qwen-primary"
    assert parsed_model.routing_roles == {"summarization"}
    assert parsed_model.worker_mode == "auxiliary"
    assert parsed_model.allow_code_execution is False
    assert parsed_model.task_family_scores["summarization"]["utility_score"] == 0.8
    assert _converted["providers"][0]["models"][0]["task_family_scores"][
        "summarization"
    ]["utility_score"] == 0.8

    # A score, role, or permission mutation must fail at the real consumer.
    for mutate in (
        lambda p: p["providers"][0].update({"routing_roles": ["full_agent"]}),
        lambda p: p["providers"][0].update({"allow_code_execution": True}),
        lambda p: p["providers"][0]["models"][0]["task_family_scores"][
            "summarization"
        ].update({"utility_score": 1.0}),
    ):
        altered = json.loads(json.dumps(document))
        mutate(altered)
        try:
            consumer.validate_projection_document(altered, now_ms=1_010_000)
        except ValueError as exc:
            assert "checksum mismatch" in str(exc)
        else:
            raise AssertionError("tampered signed routing metadata was accepted")

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

    # A missing FleetNode policy must remain an explicit observer-only
    # denial after the REAL consumer parses the signed payload.
    producer.node_routing_policy_index = lambda _factory: {}
    missing = producer.build_runtime_projection(
        lambda: None,
        private_key=private_key,
        key_id="cross-repo-test",
    )
    missing_document, _ = consumer.validate_projection_document(
        missing, now_ms=1_010_000,
    )
    missing_provider = missing_document.providers[0]
    assert missing_provider.worker_mode == "observer_only"
    assert missing_provider.models[0].worker_mode == "observer_only"
    missing_candidate = ProviderCandidate(
        provider=missing_provider,
        model=missing_provider.models[0],
        score=100.0,
    )
    for task_family in ("coding", "summarization", "general"):
        request = RouterRequest(
            request_id="cross-repo-deny-only",
            route="chat_completions",
            model="auto/fast",
            metadata={"task_family": task_family},
        )
        stage = ExecutionStage(
            purpose=StagePurpose.final,
            candidates=[missing_candidate],
        )
        assert benchmark_order(stage, request).candidates == []

    # No projection can be issued when a read-only routing graph query fails.
    def synthetic_graph_outage(_factory):
        raise OSError("synthetic graph outage")

    producer.node_routing_policy_index = synthetic_graph_outage
    try:
        producer.build_runtime_projection(
            lambda: None,
            private_key=private_key,
            key_id="cross-repo-test",
        )
    except producer.legacy.RuntimeProjectionBlocked:
        pass
    else:
        raise AssertionError("graph outage failed open")

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
            "routing_metadata_retained": True,
            "routing_metadata_tamper_rejected": True,
            "missing_policy_routing_denied": True,
            "routing_graph_outage_denied": True,
        },
    }
    output = Path(args.matrix_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    print(json.dumps(matrix, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
