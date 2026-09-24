#!/usr/bin/env python3
"""Validate a Kipnerter Fleet-model replica-loss canary evidence bundle.

The bundle deliberately combines phone-safe observations with server-side
Auto-Router route evidence. Physical provider identity remains internal while
the mobile response is checked for redaction.

Expected JSON shape:

{
  "before": {
    "catalog": {"models": [...]},
    "mobile_response": {...},
    "mobile_request_id": "kmr:...",
    "route_decision_event": {"payload": {...}},
    "route_execution_event": {"payload": {...}}
  },
  "after": {
    "catalog": {"models": [...]},
    "mobile_response": {...},
    "route_event": {"payload": {...}}
  }
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FORBIDDEN_MOBILE_KEYS = {
    "artifact_fingerprint",
    "runtime_instance_id",
    "runtime_node_id",
    "provider",
    "provider_id",
    "provider_model",
    "provider_model_id",
    "selected_access_url",
    "base_url",
    "access_urls",
}


class CanaryEvidenceError(ValueError):
    pass


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def _catalog_model(catalog: dict[str, Any], handle: str) -> dict[str, Any]:
    models = catalog.get("models")
    if not isinstance(models, list):
        raise CanaryEvidenceError("catalog models are missing")
    matches = [
        item
        for item in models
        if isinstance(item, dict) and item.get("model_handle") == handle
    ]
    if len(matches) != 1:
        raise CanaryEvidenceError(
            f"expected exactly one catalog row for {handle}, found {len(matches)}"
        )
    return matches[0]


def _assert_mobile_redaction(value: Any, path: str = "mobile_response") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) in FORBIDDEN_MOBILE_KEYS:
                raise CanaryEvidenceError(
                    f"{path} exposes forbidden mobile key {key!r}"
                )
            _assert_mobile_redaction(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_mobile_redaction(nested, f"{path}[{index}]")


def _phase(
    evidence: dict[str, Any],
    *,
    phase: str,
    expected_handle: str | None = None,
) -> dict[str, Any]:
    try:
        catalog = evidence["catalog"]
        response = evidence["mobile_response"]
        mobile_request_id = evidence["mobile_request_id"]
        decision = _payload(evidence["route_decision_event"])
        execution = _payload(evidence["route_execution_event"])
    except KeyError as exc:
        raise CanaryEvidenceError(f"{phase} is missing {exc.args[0]}") from exc

    if not all(isinstance(item, dict) for item in (catalog, response, decision, execution)):
        raise CanaryEvidenceError(f"{phase} evidence entries must be objects")
    if (
        not isinstance(mobile_request_id, str)
        or not mobile_request_id.startswith("kmr:")
    ):
        raise CanaryEvidenceError(f"{phase} lacks a valid mobile request correlation ID")

    handle = response.get("model")
    if not isinstance(handle, str) or not handle.startswith("model:v1:"):
        raise CanaryEvidenceError(f"{phase} mobile response lacks an opaque model handle")
    if expected_handle is not None and handle != expected_handle:
        raise CanaryEvidenceError(f"{phase} changed the mobile model handle")

    row = _catalog_model(catalog, handle)
    if row.get("state") != "ready":
        raise CanaryEvidenceError(f"{phase} catalog model is not ready")
    try:
        replica_count = int(row.get("ready_runtime_count"))
    except (TypeError, ValueError) as exc:
        raise CanaryEvidenceError(f"{phase} has invalid ready_runtime_count") from exc
    if replica_count < 1:
        raise CanaryEvidenceError(f"{phase} has no ready replicas")

    if decision.get("profile") != "exact_artifact":
        raise CanaryEvidenceError(f"{phase} route profile is not exact_artifact")
    for event_name, event in (("decision", decision), ("execution", execution)):
        if event.get("assistx_mobile_model_handle") != handle:
            raise CanaryEvidenceError(
                f"{phase} {event_name} event handle does not match mobile handle"
            )
        if event.get("assistx_mobile_request_id") != mobile_request_id:
            raise CanaryEvidenceError(
                f"{phase} {event_name} event does not match mobile request ID"
            )
        if event.get("local_only") is not True or event.get("allow_cloud") is not False:
            raise CanaryEvidenceError(
                f"{phase} {event_name} widened local-only routing authority"
            )

    artifact = decision.get("artifact_fingerprint")
    execution_artifact = execution.get("artifact_fingerprint")
    if not isinstance(artifact, str) or not artifact.strip():
        raise CanaryEvidenceError(f"{phase} route decision lacks artifact identity")
    if execution_artifact != artifact:
        raise CanaryEvidenceError(
            f"{phase} execution artifact does not match route decision"
        )
    if execution.get("status") != "completed":
        raise CanaryEvidenceError(f"{phase} route execution did not complete")
    status_code = execution.get("status_code")
    if not isinstance(status_code, int) or not (200 <= status_code < 400):
        raise CanaryEvidenceError(f"{phase} route execution lacks a successful status code")

    provider = execution.get("provider_id") or execution.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise CanaryEvidenceError(f"{phase} execution event lacks serving replica")

    _assert_mobile_redaction(response)

    return {
        "handle": handle,
        "artifact_fingerprint": artifact,
        "provider": provider,
        "mobile_request_id": mobile_request_id,
        "ready_runtime_count": replica_count,
        "request_id": execution.get("request_id"),
        "correlation_id": execution.get("correlation_id"),
    }


def validate_evidence(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise CanaryEvidenceError("evidence bundle must be a JSON object")
    if not isinstance(bundle.get("before"), dict) or not isinstance(bundle.get("after"), dict):
        raise CanaryEvidenceError("evidence bundle requires before and after objects")

    before = _phase(bundle["before"], phase="before")
    after = _phase(
        bundle["after"],
        phase="after",
        expected_handle=before["handle"],
    )

    if after["artifact_fingerprint"] != before["artifact_fingerprint"]:
        raise CanaryEvidenceError("artifact authority changed across replica loss")
    if after["provider"] == before["provider"]:
        raise CanaryEvidenceError("chosen replica did not change")
    if after["mobile_request_id"] == before["mobile_request_id"]:
        raise CanaryEvidenceError("before and after requests reused one correlation ID")
    if before["ready_runtime_count"] < 2:
        raise CanaryEvidenceError("before evidence does not prove a replicated model")
    if after["ready_runtime_count"] >= before["ready_runtime_count"]:
        raise CanaryEvidenceError("after evidence does not prove replica loss")

    return {
        "result": "PASS",
        "model_handle": before["handle"],
        "artifact_fingerprint": before["artifact_fingerprint"],
        "before_provider": before["provider"],
        "after_provider": after["provider"],
        "before_mobile_request_id": before["mobile_request_id"],
        "after_mobile_request_id": after["mobile_request_id"],
        "before_ready_runtime_count": before["ready_runtime_count"],
        "after_ready_runtime_count": after["ready_runtime_count"],
        "before_request_id": before["request_id"],
        "after_request_id": after["request_id"],
        "before_correlation_id": before["correlation_id"],
        "after_correlation_id": after["correlation_id"],
        "authority_invariant": (
            "same_handle_same_artifact_different_replica_no_authority_widening"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    bundle = json.loads(args.evidence.read_text(encoding="utf-8"))
    try:
        result = validate_evidence(bundle)
    except CanaryEvidenceError as exc:
        print(json.dumps({"result": "FAIL", "error": str(exc)}, indent=2))
        return 1

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
