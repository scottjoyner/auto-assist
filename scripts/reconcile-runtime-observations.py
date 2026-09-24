#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_RUNTIME_KIND_ALIASES = {
    "lmstudio": "lmstudio",
    "lm_studio": "lmstudio",
    "lm studio": "lmstudio",
    "llama.cpp": "llama_cpp",
    "llama_cpp": "llama_cpp",
    "llamacpp": "llama_cpp",
    "vllm": "vllm",
    "sglang": "sglang",
    "openai_compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
}

_REQUIRED_ADMISSION_EVIDENCE = [
    "physical_runtime_identity",
    "runtime_version_and_process_id",
    "model_artifact_fingerprint",
    "model_quantization_and_context",
    "capacity_observation",
    "approved_access_paths",
    "lan_preference_and_tailscale_fallback",
    "runtime_canary_soak",
    "runtime_canary_rollback",
    "operator_approval",
]


def _kind(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _RUNTIME_KIND_ALIASES.get(raw, raw or "unknown")


def _port(value: Any) -> int | None:
    try:
        parsed = urlparse(str(value or "").strip())
        if parsed.port is not None:
            return int(parsed.port)
        if parsed.scheme == "https":
            return 443
        if parsed.scheme == "http":
            return 80
    except (ValueError, TypeError):
        return None
    return None


def _host(value: Any) -> str:
    try:
        return str(urlparse(str(value or "").strip()).hostname or "").strip().lower()
    except ValueError:
        return ""


def _node_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _provider_ports(provider: dict[str, Any]) -> set[int]:
    urls: list[Any] = []
    if provider.get("base_url"):
        urls.append(provider["base_url"])
    urls.extend(provider.get("access_urls") or [])
    return {port for url in urls if (port := _port(url)) is not None}


def _provider_hosts(provider: dict[str, Any]) -> set[str]:
    urls: list[Any] = []
    if provider.get("base_url"):
        urls.append(provider["base_url"])
    urls.extend(provider.get("access_urls") or [])
    return {host for url in urls if (host := _host(url))}


def _provider_model_groups(
    provider: dict[str, Any],
) -> list[tuple[str, set[str], list[str]]]:
    groups: list[tuple[str, set[str], list[str]]] = []
    for model in provider.get("models") or []:
        if not isinstance(model, dict):
            continue
        raw_ids = [
            str(model.get("alias") or "").strip(),
            str(model.get("provider_model") or "").strip(),
        ]
        ids = {value.casefold() for value in raw_ids if value}
        if not ids:
            continue
        canonical = raw_ids[1] or raw_ids[0]
        fingerprints = sorted(
            {
                str(model.get("artifact_fingerprint") or "").strip()
            }
            - {""}
        )
        groups.append((canonical, ids, fingerprints))
    return groups


def _projection_indexes(
    projection: dict[str, Any],
) -> tuple[
    dict[tuple[str, int, str], list[dict[str, Any]]],
    dict[tuple[str, int], list[dict[str, Any]]],
]:
    exact: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    endpoint: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for provider in projection.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("enabled") is False:
            continue
        node = _node_id(provider.get("node_id") or provider.get("hostname"))
        runtime_kind = _kind(provider.get("runtime_kind") or provider.get("type"))
        if not node or runtime_kind == "unknown":
            continue
        for port in _provider_ports(provider):
            exact.setdefault((node, port, runtime_kind), []).append(provider)
            endpoint.setdefault((node, port), []).append(provider)
    return exact, endpoint


def _runtime_ids(providers: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(provider.get("runtime_instance_id") or provider.get("name") or "")
            for provider in providers
        }
    )


def _source_ip_match(
    provider: dict[str, Any],
    source_ip: Any,
) -> bool | None:
    raw = str(source_ip or "").strip()
    if not raw:
        return None
    try:
        source = ipaddress.ip_address(raw)
    except ValueError:
        return None

    provider_ips = set()
    for host in _provider_hosts(provider):
        try:
            provider_ips.add(ipaddress.ip_address(host))
        except ValueError:
            continue
    if not provider_ips:
        return None
    return source in provider_ips


def _freshness(
    *,
    observed_at: int,
    received_at: int,
    now_seconds: int,
    max_age_seconds: int,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    future_tolerance = 30
    for label, stamp in (
        ("observation", observed_at),
        ("router_receipt", received_at),
    ):
        if stamp <= 0:
            reasons.append(f"{label}_timestamp_missing")
            continue
        age = now_seconds - stamp
        if age > max_age_seconds:
            reasons.append(f"{label}_stale")
        elif age < -future_tolerance:
            reasons.append(f"{label}_timestamp_in_future")
    return not reasons, reasons


def reconcile(
    nodes_payload: dict[str, Any],
    projection: dict[str, Any],
    *,
    now_seconds: int | None = None,
    max_observation_age_seconds: int = 180,
    projection_verified: bool = False,
) -> dict[str, Any]:
    """Compare fresh runtime observations with one signed admitted projection.

    Reconciliation is deliberately non-mutating. Observation remains discovery
    evidence and can never create admission or routing authority.
    """

    now = int(now_seconds if now_seconds is not None else time.time())
    max_age = max(1, int(max_observation_age_seconds))
    exact_index, endpoint_index = _projection_indexes(projection)
    items: list[dict[str, Any]] = []
    observed_keys: set[tuple[str, int, str]] = set()

    for node_report in nodes_payload.get("nodes") or []:
        if not isinstance(node_report, dict):
            continue
        node = _node_id(
            node_report.get("hostname")
            or node_report.get("host_name")
            or node_report.get("node_id")
        )
        if not node:
            continue
        try:
            received_at = int(node_report.get("received_at") or 0)
        except (TypeError, ValueError):
            received_at = 0
        source_ip = str(node_report.get("source_ip") or "").strip()
        observation_set_truncated = bool(
            node_report.get("runtime_observations_truncated")
        )

        for observation in node_report.get("runtimes") or []:
            if not isinstance(observation, dict):
                continue
            if observation.get("observation_schema") != "fleet-runtime-observation.v1":
                continue
            if observation.get("admitted") is True:
                # Reporter/router contracts force this false. Refuse to treat an
                # observation claiming admission as trustworthy evidence.
                continue

            runtime_kind = _kind(observation.get("runtime_kind"))
            port = _port(observation.get("base_url"))
            if port is None:
                continue
            key = (node, port, runtime_kind)
            observed_keys.add(key)
            matches = exact_index.get(key, [])
            endpoint_matches = endpoint_index.get((node, port), [])
            observed_models = sorted(
                {
                    str(value).strip()
                    for value in (observation.get("models") or [])
                    if str(value).strip()
                },
                key=str.casefold,
            )
            try:
                observed_at = int(observation.get("observed_at") or 0)
            except (TypeError, ValueError):
                observed_at = 0
            fresh, freshness_reasons = _freshness(
                observed_at=observed_at,
                received_at=received_at,
                now_seconds=now,
                max_age_seconds=max_age,
            )
            ready = bool(observation.get("ready")) and bool(observed_models)
            models_truncated = bool(observation.get("models_truncated"))

            status: str
            action: str
            reasons: list[str] = []
            unexpected_models: list[str] = []
            missing_projected_models: list[str] = []
            matched_runtime_ids: list[str] = []
            projected_artifact_fingerprints: list[str] = []
            node_source_match: bool | None = None

            if len(matches) > 1:
                status = "ambiguous_projection_match"
                action = "review_runtime_identity"
                reasons.append("multiple_signed_providers_match_node_port_kind")
                matched_runtime_ids = _runtime_ids(matches)
            elif not matches and endpoint_matches:
                status = "runtime_identity_mismatch"
                action = "review_runtime_identity"
                reasons.append("signed_provider_exists_on_node_port_with_different_runtime_kind")
                matched_runtime_ids = _runtime_ids(endpoint_matches)
            elif not matches:
                status = "unprojected_runtime"
                action = "collect_admission_evidence"
                reasons.append("no_signed_provider_matches_node_port_kind")
            else:
                provider = matches[0]
                matched_runtime_ids = _runtime_ids(matches)
                node_source_match = _source_ip_match(provider, source_ip)
                groups = _provider_model_groups(provider)
                projected_union = {
                    model_id
                    for _canonical, ids, _fingerprints in groups
                    for model_id in ids
                }
                observed_cf = {model.casefold() for model in observed_models}
                unexpected_models = [
                    model
                    for model in observed_models
                    if model.casefold() not in projected_union
                ]
                for canonical, ids, fingerprints in groups:
                    projected_artifact_fingerprints.extend(fingerprints)
                    if not (ids & observed_cf):
                        missing_projected_models.append(canonical)

                if node_source_match is False:
                    status = "runtime_identity_mismatch"
                    action = "review_runtime_identity"
                    reasons.append("transport_source_ip_not_in_signed_access_paths")
                elif not fresh:
                    status = "stale_observation"
                    action = "refresh_runtime_observation"
                    reasons.extend(freshness_reasons)
                elif observation_set_truncated or models_truncated:
                    status = "incomplete_observation"
                    action = "refresh_runtime_observation"
                    if observation_set_truncated:
                        reasons.append("runtime_observation_set_truncated")
                    if models_truncated:
                        reasons.append("runtime_model_set_truncated")
                elif not ready:
                    status = "runtime_not_ready"
                    action = "collect_runtime_health_evidence"
                    reasons.append("runtime_not_ready_or_no_models")
                elif unexpected_models or missing_projected_models:
                    status = "model_drift"
                    action = "collect_model_identity_evidence"
                    if unexpected_models:
                        reasons.append("unexpected_observed_models")
                    if missing_projected_models:
                        reasons.append("projected_models_not_observed")
                else:
                    status = "projected"
                    action = "none"

            items.append(
                {
                    "node_id": node,
                    "source_ip": source_ip or None,
                    "node_source_match": node_source_match,
                    "runtime_observation_id": str(
                        observation.get("runtime_observation_id") or ""
                    ),
                    "runtime_kind": runtime_kind,
                    "port": port,
                    "observed_models": observed_models,
                    "observed_at": observed_at,
                    "router_received_at": received_at,
                    "fresh": fresh,
                    "ready": ready,
                    "models_truncated": models_truncated,
                    "observation_set_truncated": observation_set_truncated,
                    "status": status,
                    "action": action,
                    "reason_codes": reasons,
                    "matched_runtime_ids": matched_runtime_ids,
                    "unexpected_models": unexpected_models,
                    "missing_models": unexpected_models,
                    "missing_projected_models": missing_projected_models,
                    "projected_artifact_fingerprints": sorted(
                        set(projected_artifact_fingerprints)
                    ),
                    # Endpoint/model-name reconciliation cannot prove that the
                    # bytes currently loaded are the signed artifact.
                    "artifact_identity_verified": False,
                    "identity_evidence_level": "endpoint_and_model_names",
                    "required_admission_evidence": (
                        list(_REQUIRED_ADMISSION_EVIDENCE)
                        if status == "unprojected_runtime"
                        else []
                    ),
                }
            )

    projected_without_observation: list[dict[str, Any]] = []
    for key, providers in sorted(exact_index.items()):
        if key in observed_keys:
            continue
        node, port, runtime_kind = key
        projected_without_observation.append(
            {
                "node_id": node,
                "port": port,
                "runtime_kind": runtime_kind,
                "runtime_instance_ids": _runtime_ids(providers),
            }
        )

    items.sort(
        key=lambda item: (
            str(item["node_id"]),
            int(item["port"]),
            str(item["runtime_kind"]),
        )
    )
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    statuses = (
        "projected",
        "unprojected_runtime",
        "model_drift",
        "ambiguous_projection_match",
        "runtime_identity_mismatch",
        "runtime_not_ready",
        "stale_observation",
        "incomplete_observation",
    )
    return {
        "schema_version": "assistx.runtime-observation-reconciliation.v2",
        "projection_generation": projection.get("generation"),
        "projection_revision": projection.get("revision"),
        "projection_checksum": projection.get("checksum"),
        "projection_signature_key_id": projection.get("signature_key_id"),
        "projection_verified": bool(projection_verified),
        "summary": {
            "observed_runtime_count": len(items),
            **{status: counts.get(status, 0) for status in statuses},
            "projected_without_observation": len(projected_without_observation),
        },
        "items": items,
        "projected_without_observation": projected_without_observation,
        "mutating": False,
        "admission_authority": False,
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_projection(projection: dict[str, Any], verify_key_file: Path) -> None:
    if str(projection.get("schema_version") or "") != "2":
        raise ValueError("operator reconciliation requires schema-v2 Ed25519 projection")
    src = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(src))
    from assistx.runtime_projection_v2 import verify_projection_v2

    verify_projection_v2(
        projection,
        verify_key_file=str(verify_key_file),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Auto-Router fleet runtime observations with one verified "
            "AssistX signed runtime projection. This command is read-only and "
            "never admits a runtime."
        )
    )
    parser.add_argument("--nodes", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--verify-key-file", type=Path, required=True)
    parser.add_argument(
        "--max-observation-age-seconds",
        type=int,
        default=180,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    nodes = _load(args.nodes)
    projection = _load(args.projection)
    _verify_projection(projection, args.verify_key_file)
    result = reconcile(
        nodes,
        projection,
        max_observation_age_seconds=args.max_observation_age_seconds,
        projection_verified=True,
    )
    result["input_sha256"] = {
        "nodes": _sha256_file(args.nodes),
        "projection": _sha256_file(args.projection),
        "verify_key": _sha256_file(args.verify_key_file),
    }

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    summary = result["summary"]
    print(
        "RUNTIME_OBSERVATION_RECONCILIATION: "
        f"observed={summary['observed_runtime_count']} "
        f"projected={summary['projected']} "
        f"unprojected={summary['unprojected_runtime']} "
        f"model_drift={summary['model_drift']} "
        f"identity_mismatch={summary['runtime_identity_mismatch']} "
        f"not_ready={summary['runtime_not_ready']} "
        f"stale={summary['stale_observation']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
