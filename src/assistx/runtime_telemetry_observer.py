from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import urlencode

import requests

SNAPSHOT_SCHEMA = "assistx-runtime-telemetry-snapshot-v1"
JOIN_SCHEMA = "assistx-runtime-telemetry-join-v1"

_IDENTITY_FIELDS = (
    "node_id",
    "model_handle",
    "backend",
    "quantization",
    "speculation",
    "runtime_revision",
    "launch_config_sha256",
    "process_started_at_unix_ms",
)

_COUNTER_FIELDS = (
    "requests_total",
    "prefill_tokens",
    "prefill_seconds",
    "cache_reused_tokens",
    "decode_tokens",
    "decode_seconds",
    "spec_proposed_tokens",
    "spec_accepted_tokens",
    "spec_verification_steps",
    "verification_tokens",
    "verification_seconds",
    "cache_hits",
    "cache_misses",
    "cache_reprocessed_tokens",
    "energy_joules",
)

_GAUGE_FIELDS = (
    "vram_bytes",
    "vram_peak_bytes",
    "power_watts",
    "gpu_utilization_percent",
    "requests_processing",
    "requests_deferred",
    "prefill_tokens_per_second",
    "decode_tokens_per_second",
    "busy_slots_per_decode",
)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_runtime_snapshot(
    policy: dict[str, Any],
    *,
    trial_id: str,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    telemetry_env = str(policy.get("telemetry_env") or "").strip()
    if not telemetry_env:
        return {
            "configured": False,
            "valid": False,
            "reason": "telemetry_not_configured",
        }

    base_url = os.getenv(telemetry_env, "").strip()
    if not base_url:
        return {
            "configured": True,
            "valid": False,
            "reason": f"environment variable {telemetry_env} is not set",
        }

    headers: dict[str, str] = {}
    token_env = str(policy.get("telemetry_token_env") or "").strip()
    if token_env:
        token = os.getenv(token_env, "")
        if not token:
            return {
                "configured": True,
                "valid": False,
                "reason": f"environment variable {token_env} is not set",
            }
        headers["Authorization"] = f"Bearer {token}"

    url = _snapshot_url(base_url, trial_id)
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=max(1.0, float(timeout_s)),
        )
        response.raise_for_status()
        raw = response.json()
    except Exception as exc:
        return {
            "configured": True,
            "valid": False,
            "reason": f"telemetry_request_failed: {str(exc)[:400]}",
        }

    try:
        snapshot = validate_runtime_snapshot(
            raw,
            policy,
            trial_id=trial_id,
        )
    except Exception as exc:
        return {
            "configured": True,
            "valid": False,
            "reason": f"telemetry_validation_failed: {str(exc)[:400]}",
            "raw_snapshot_sha256": canonical_sha256(raw),
        }

    return {
        "configured": True,
        "valid": True,
        "snapshot": snapshot,
        "snapshot_sha256": snapshot["snapshot_sha256"],
    }


def validate_runtime_snapshot(
    raw: Any,
    policy: dict[str, Any],
    *,
    trial_id: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("snapshot must be an object")
    if str(raw.get("schema") or "") != SNAPSHOT_SCHEMA:
        raise ValueError(f"schema must be {SNAPSHOT_SCHEMA}")

    identity = raw.get("identity")
    counters = raw.get("counters")
    gauges = raw.get("gauges")
    if not isinstance(identity, dict):
        raise ValueError("identity must be an object")
    if not isinstance(counters, dict):
        raise ValueError("counters must be an object")
    if not isinstance(gauges, dict):
        raise ValueError("gauges must be an object")

    required_identity = _IDENTITY_FIELDS
    missing = [
        field
        for field in required_identity
        if identity.get(field) in (None, "")
    ]
    if missing:
        raise ValueError(
            "identity missing required fields: " + ", ".join(missing)
        )

    expected = {
        "node_id": str(policy["node_id"]),
        "model_handle": str(policy["model_handle"]),
        "backend": str(policy["backend"]),
        "quantization": str(policy["quantization"]),
        "speculation": str(policy["speculation"]),
    }
    mismatches = {
        key: {
            "expected": value,
            "observed": str(identity.get(key)),
        }
        for key, value in expected.items()
        if str(identity.get(key)) != value
    }
    if mismatches:
        raise ValueError(
            "runtime identity mismatch: "
            + json.dumps(mismatches, sort_keys=True)
        )

    launch_sha = str(identity["launch_config_sha256"])
    if (
        len(launch_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in launch_sha.lower())
    ):
        raise ValueError("launch_config_sha256 must be 64 lowercase hex chars")

    _validate_expected_env(
        policy,
        "expected_runtime_revision_env",
        str(identity["runtime_revision"]),
    )
    _validate_expected_env(
        policy,
        "expected_launch_config_sha256_env",
        launch_sha,
    )

    observed_at = _number(raw.get("observed_at_unix_ms"))
    if observed_at is None or observed_at <= 0:
        raise ValueError("observed_at_unix_ms must be positive")

    normalized_counters = _normalize_numeric_map(
        counters,
        _COUNTER_FIELDS,
    )
    normalized_gauges = _normalize_numeric_map(
        gauges,
        _GAUGE_FIELDS,
    )

    scope = str(raw.get("scope") or "process")
    snapshot_trial_id = raw.get("trial_id")
    if scope == "trial" and str(snapshot_trial_id or "") != trial_id:
        raise ValueError(
            "trial-scoped telemetry does not match the benchmark trial"
        )
    if scope not in {"process", "trial"}:
        raise ValueError("scope must be process or trial")

    normalized = {
        "schema": SNAPSHOT_SCHEMA,
        "scope": scope,
        "trial_id": (
            str(snapshot_trial_id)
            if snapshot_trial_id is not None
            else None
        ),
        "observed_at_unix_ms": int(observed_at),
        "identity": {
            field: identity[field]
            for field in _IDENTITY_FIELDS
        },
        "counters": normalized_counters,
        "gauges": normalized_gauges,
        "source": (
            raw.get("source")
            if isinstance(raw.get("source"), dict)
            else {}
        ),
    }
    normalized["snapshot_sha256"] = canonical_sha256(normalized)
    return normalized


def join_runtime_snapshots(
    before_capture: dict[str, Any],
    after_capture: dict[str, Any],
    policy: dict[str, Any],
    *,
    trial_id: str,
) -> dict[str, Any]:
    if not before_capture.get("valid"):
        return _invalid_join(
            "before_snapshot_invalid",
            before_capture.get("reason"),
        )
    if not after_capture.get("valid"):
        return _invalid_join(
            "after_snapshot_invalid",
            after_capture.get("reason"),
        )

    before = before_capture["snapshot"]
    after = after_capture["snapshot"]
    identity_before = before["identity"]
    identity_after = after["identity"]

    changed_identity = {
        field: {
            "before": identity_before.get(field),
            "after": identity_after.get(field),
        }
        for field in _IDENTITY_FIELDS
        if identity_before.get(field) != identity_after.get(field)
    }
    if changed_identity:
        return _invalid_join(
            "runtime_identity_changed",
            changed_identity,
        )

    if before["scope"] != after["scope"]:
        return _invalid_join(
            "telemetry_scope_changed",
            {
                "before": before["scope"],
                "after": after["scope"],
            },
        )

    deltas: dict[str, float] = {}
    counter_regressions: dict[str, Any] = {}
    for field in _COUNTER_FIELDS:
        before_value = before["counters"].get(field)
        after_value = after["counters"].get(field)
        if before_value is None or after_value is None:
            continue
        delta = after_value - before_value
        if delta < -1e-9:
            counter_regressions[field] = {
                "before": before_value,
                "after": after_value,
            }
            continue
        deltas[field] = round(delta, 9)

    if counter_regressions:
        return _invalid_join(
            "counter_regression",
            counter_regressions,
        )

    request_delta = deltas.get("requests_total")
    contamination = None
    if (
        before["scope"] == "process"
        and request_delta is not None
        and request_delta != 1
    ):
        contamination = {
            "reason": "process_scope_request_delta_not_one",
            "requests_total_delta": request_delta,
        }

    derived = _derive_metrics(deltas)
    gauges = {
        field: {
            "before": before["gauges"].get(field),
            "after": after["gauges"].get(field),
        }
        for field in _GAUGE_FIELDS
        if (
            before["gauges"].get(field) is not None
            or after["gauges"].get(field) is not None
        )
    }

    valid = contamination is None
    return {
        "schema": JOIN_SCHEMA,
        "valid": valid,
        "reason": (
            None
            if valid
            else contamination["reason"]
        ),
        "trial_id": trial_id,
        "policy_id": policy["policy_id"],
        "scope": before["scope"],
        "runtime_revision": identity_before["runtime_revision"],
        "launch_config_sha256": identity_before[
            "launch_config_sha256"
        ],
        "process_started_at_unix_ms": identity_before[
            "process_started_at_unix_ms"
        ],
        "before_snapshot_sha256": before["snapshot_sha256"],
        "after_snapshot_sha256": after["snapshot_sha256"],
        "counter_deltas": deltas,
        "gauge_samples": gauges,
        "derived": derived,
        "contamination": contamination,
        "routing_authority_changed": False,
    }


def correlate_join_with_result(
    joined: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Cross-check process-wide deltas against the exact request result.

    This is strongest for llama.cpp, whose streamed response exposes usage and
    timings while /metrics exposes cumulative counters. A mismatch means other
    work may have touched the same process during the trial, so the telemetry
    evidence is rejected rather than attributed to the benchmark request.
    """
    if not joined.get("valid"):
        return joined

    deltas = joined.get("counter_deltas")
    if not isinstance(deltas, dict):
        return joined

    timings = result.get("response_timings")
    if not isinstance(timings, dict):
        timings = {}

    checks: dict[str, dict[str, Any]] = {}
    _add_consistency_check(
        checks,
        "decode_tokens",
        deltas.get("decode_tokens"),
        result.get("completion_tokens")
        if result.get("completion_tokens") is not None
        else timings.get("predicted_n"),
    )
    _add_consistency_check(
        checks,
        "prefill_tokens",
        deltas.get("prefill_tokens"),
        timings.get("prompt_n"),
    )
    _add_consistency_check(
        checks,
        "spec_proposed_tokens",
        deltas.get("spec_proposed_tokens"),
        timings.get("draft_n"),
    )
    _add_consistency_check(
        checks,
        "spec_accepted_tokens",
        deltas.get("spec_accepted_tokens"),
        timings.get("draft_n_accepted"),
    )

    checked = [
        value
        for value in checks.values()
        if value.get("checked") is True
    ]
    joined["request_consistency"] = {
        "checked": bool(checked),
        "checks": checks,
    }
    failed = [
        name
        for name, value in checks.items()
        if value.get("checked") is True
        and value.get("passed") is not True
    ]
    if failed:
        joined["valid"] = False
        joined["reason"] = "process_scope_result_mismatch"
        joined["request_consistency"]["failed"] = failed
    return joined


def _add_consistency_check(
    checks: dict[str, dict[str, Any]],
    name: str,
    observed: Any,
    expected: Any,
) -> None:
    observed_value = _number(observed)
    expected_value = _number(expected)
    if observed_value is None or expected_value is None:
        checks[name] = {"checked": False}
        return
    # Token counters should match exactly for an isolated single request.
    # Float conversion is used only because Prometheus numbers are float64.
    passed = abs(observed_value - expected_value) < 1e-9
    checks[name] = {
        "checked": True,
        "passed": passed,
        "observed_process_delta": observed_value,
        "expected_request_value": expected_value,
    }


def _derive_metrics(deltas: dict[str, float]) -> dict[str, float | None]:
    proposed = deltas.get("spec_proposed_tokens")
    accepted = deltas.get("spec_accepted_tokens")
    prefill_tokens = deltas.get("prefill_tokens")
    prefill_seconds = deltas.get("prefill_seconds")
    decode_tokens = deltas.get("decode_tokens")
    decode_seconds = deltas.get("decode_seconds")
    verification_tokens = deltas.get("verification_tokens")
    verification_seconds = deltas.get("verification_seconds")

    return {
        "spec_acceptance_rate": _ratio(accepted, proposed),
        "prefill_tokens_per_second": _rate(
            prefill_tokens,
            prefill_seconds,
        ),
        "decode_tokens_per_second": _rate(
            decode_tokens,
            decode_seconds,
        ),
        "verification_tokens_per_second": _rate(
            verification_tokens,
            verification_seconds,
        ),
        "joules_per_decode_token": _ratio(
            deltas.get("energy_joules"),
            decode_tokens,
        ),
        "cache_reuse_rate": _ratio(
            deltas.get("cache_reused_tokens"),
            prefill_tokens,
        ),
    }


def _invalid_join(reason: str, detail: Any) -> dict[str, Any]:
    return {
        "schema": JOIN_SCHEMA,
        "valid": False,
        "reason": reason,
        "detail": detail,
        "routing_authority_changed": False,
    }


def _validate_expected_env(
    policy: dict[str, Any],
    key: str,
    observed: str,
) -> None:
    env_name = str(policy.get(key) or "").strip()
    if not env_name:
        return
    expected = os.getenv(env_name, "").strip()
    if not expected:
        raise ValueError(f"environment variable {env_name} is not set")
    if observed != expected:
        raise ValueError(
            f"{key} mismatch: expected {expected!r}, observed {observed!r}"
        )


def _snapshot_url(base_url: str, trial_id: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1/telemetry/snapshot"):
        return base + "?" + urlencode({"trial_id": trial_id})
    return (
        base
        + "/v1/telemetry/snapshot?"
        + urlencode({"trial_id": trial_id})
    )


def _normalize_numeric_map(
    raw: dict[str, Any],
    fields: tuple[str, ...],
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for field in fields:
        result[field] = _number(raw.get(field))
    return result


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result


def _ratio(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    if (
        numerator is None
        or denominator is None
        or denominator <= 0
    ):
        return None
    return round(numerator / denominator, 9)


def _rate(
    count: float | None,
    seconds: float | None,
) -> float | None:
    if count is None or seconds is None or seconds <= 0:
        return None
    return round(count / seconds, 6)
