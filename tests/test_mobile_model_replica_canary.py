from __future__ import annotations

import pytest

from scripts.validate_mobile_model_replica_canary import (
    CanaryEvidenceError,
    validate_evidence,
)


HANDLE = "model:v1:" + "a" * 32
ARTIFACT = "sha256:bonsai"


def _catalog(count: int) -> dict:
    return {
        "models": [
            {
                "model_handle": HANDLE,
                "display_name": "Ternary Bonsai 2",
                "state": "ready",
                "ready_runtime_count": count,
            }
        ]
    }


def _mobile_response() -> dict:
    return {
        "id": "kipnerter-fleet-model",
        "object": "chat.completion",
        "model": HANDLE,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
    }


def _route_event(provider: str, request_id: str, mobile_request_id: str) -> dict:
    return {
        "payload": {
            "request_id": request_id,
            "correlation_id": f"corr-{request_id}",
            "profile": "exact_artifact",
            "assistx_mobile_model_handle": HANDLE,
            "assistx_mobile_request_id": mobile_request_id,
            "artifact_fingerprint": ARTIFACT,
            "local_only": True,
            "allow_cloud": False,
            "chosen": {
                "provider": provider,
                "provider_id": provider,
            },
        }
    }


def _bundle() -> dict:
    return {
        "before": {
            "catalog": _catalog(2),
            "mobile_response": _mobile_response(),
            "mobile_request_id": "kmr:before",
            "route_event": _route_event("runtime-a", "before", "kmr:before"),
        },
        "after": {
            "catalog": _catalog(1),
            "mobile_response": _mobile_response(),
            "mobile_request_id": "kmr:after",
            "route_event": _route_event("runtime-b", "after", "kmr:after"),
        },
    }


def test_replica_canary_accepts_same_handle_artifact_and_new_provider() -> None:
    result = validate_evidence(_bundle())

    assert result["result"] == "PASS"
    assert result["model_handle"] == HANDLE
    assert result["artifact_fingerprint"] == ARTIFACT
    assert result["before_provider"] == "runtime-a"
    assert result["after_provider"] == "runtime-b"
    assert result["before_ready_runtime_count"] == 2
    assert result["after_ready_runtime_count"] == 1
    assert result["authority_invariant"] == (
        "same_handle_same_artifact_different_replica_no_authority_widening"
    )


def test_replica_canary_rejects_artifact_drift() -> None:
    bundle = _bundle()
    bundle["after"]["route_event"]["payload"]["artifact_fingerprint"] = "sha256:other"

    with pytest.raises(CanaryEvidenceError, match="artifact authority changed"):
        validate_evidence(bundle)


def test_replica_canary_rejects_mobile_runtime_coordinate_leak() -> None:
    bundle = _bundle()
    bundle["after"]["mobile_response"]["runtime_instance_id"] = "llama:38898"

    with pytest.raises(CanaryEvidenceError, match="forbidden mobile key"):
        validate_evidence(bundle)


def test_replica_canary_rejects_same_serving_replica() -> None:
    bundle = _bundle()
    bundle["after"]["route_event"] = _route_event("runtime-a", "after", "kmr:after")

    with pytest.raises(CanaryEvidenceError, match="chosen replica did not change"):
        validate_evidence(bundle)


def test_replica_canary_rejects_mismatched_request_correlation() -> None:
    bundle = _bundle()
    bundle["after"]["route_event"]["payload"]["assistx_mobile_request_id"] = "kmr:other"

    with pytest.raises(CanaryEvidenceError, match="does not match mobile request ID"):
        validate_evidence(bundle)
