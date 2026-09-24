#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
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


_WITNESS_SCHEMA = "fleet-runtime-identity-witness.v1"
_WITNESS_NAMESPACE = "lms-runtime-identity-witness"
_SIGNER_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,255}$")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_witness_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _verify_runtime_identity_witness(
    payload: str,
    signature: str,
    *,
    allowed_signers: Path,
    identity: str,
    namespace: str = _WITNESS_NAMESPACE,
) -> dict[str, Any]:
    if not _SIGNER_IDENTITY_RE.fullmatch(identity):
        raise ValueError("runtime witness signer identity is invalid")
    if not allowed_signers.is_file() or allowed_signers.is_symlink():
        raise ValueError("runtime witness allowed-signers file is unavailable")
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise ValueError("ssh-keygen is required to verify runtime identity witnesses")
    if len(payload.encode("utf-8")) > 16 * 1024:
        raise ValueError("runtime identity witness exceeds size bound")
    if len(signature.encode("utf-8")) > 8 * 1024:
        raise ValueError("runtime identity witness signature exceeds size bound")
    try:
        witness = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("runtime identity witness is not valid JSON") from exc
    if not isinstance(witness, dict) or witness.get("schema_version") != _WITNESS_SCHEMA:
        raise ValueError("unsupported runtime identity witness schema")
    if witness.get("admission") != {"admitted": False}:
        raise ValueError("runtime identity witness must remain non-admitted")
    process_identity = witness.get("process")
    model_file_identity = witness.get("model_file_identity")
    if not isinstance(process_identity, dict) or not isinstance(model_file_identity, dict):
        raise ValueError("runtime identity witness process/model file identity is missing")
    try:
        if (
            int(process_identity.get("pid") or 0) <= 0
            or int(process_identity.get("process_start_ticks") or 0) <= 0
            or int(model_file_identity.get("inode") or 0) <= 0
            or int(model_file_identity.get("size_bytes") or 0) <= 0
            or int(model_file_identity.get("mtime_ns") or 0) <= 0
            or int(model_file_identity.get("ctime_ns") or 0) <= 0
        ):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime identity witness process/model file identity is invalid") from exc
    if str(witness.get("model_process_binding") or "") not in {"proc_maps", "cmdline"}:
        raise ValueError("runtime identity witness model-process binding is invalid")
    fingerprint = str(witness.get("witness_fingerprint") or "")
    core = {key: value for key, value in witness.items() if key != "witness_fingerprint"}
    if fingerprint != _canonical_hash(core):
        raise ValueError("runtime identity witness fingerprint mismatch")
    if _canonical_witness_bytes(witness) != payload.encode("utf-8"):
        raise ValueError("runtime identity witness is not canonical JSON")
    if "BEGIN SSH SIGNATURE" not in signature or "END SSH SIGNATURE" not in signature:
        raise ValueError("runtime identity witness signature is malformed")

    with tempfile.TemporaryDirectory(prefix="assistx-runtime-witness-") as directory:
        signature_path = Path(directory) / "witness.sig"
        signature_path.write_text(signature, encoding="utf-8")
        process = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                identity,
                "-n",
                namespace,
                "-s",
                str(signature_path),
            ],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    if process.returncode != 0:
        stderr = process.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError("runtime identity witness signature verification failed: " + stderr)
    return witness


def _verify_node_witnesses(
    nodes_payload: dict[str, Any],
    *,
    allowed_signers: Path,
    identity: str,
    namespace: str = _WITNESS_NAMESPACE,
) -> None:
    for node_report in nodes_payload.get("nodes") or []:
        if not isinstance(node_report, dict):
            continue
        for observation in node_report.get("runtimes") or []:
            if not isinstance(observation, dict):
                continue
            payload = observation.get("runtime_identity_witness_json")
            signature = observation.get("runtime_identity_witness_signature")
            if payload is None and signature is None:
                continue
            if not isinstance(payload, str) or not isinstance(signature, str):
                observation["_runtime_identity_witness_error"] = "witness_payload_or_signature_missing"
                continue
            try:
                verified = _verify_runtime_identity_witness(
                    payload,
                    signature,
                    allowed_signers=allowed_signers,
                    identity=identity,
                    namespace=namespace,
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                observation["_runtime_identity_witness_error"] = str(exc)[:500]
                continue
            observation["_verified_runtime_identity_witness"] = verified
            observation["_runtime_identity_witness_signature_verified"] = True


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

            observed_runtime_kind = _kind(observation.get("runtime_kind"))
            verified_witness = observation.get("_verified_runtime_identity_witness")
            witness_kind = (
                _kind(verified_witness.get("runtime_kind"))
                if isinstance(verified_witness, dict)
                else "unknown"
            )
            runtime_kind = observed_runtime_kind
            if (
                observed_runtime_kind == "openai_compatible"
                and witness_kind
                in {"lmstudio", "llama_cpp", "vllm", "sglang", "openai_compatible"}
            ):
                runtime_kind = witness_kind
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
            artifact_identity_verified = False
            artifact_identity_reason = "signed_runtime_witness_missing"
            witness_loadout_fingerprint: str | None = None
            witness_model_content_sha256: str | None = None
            witness_signing_key_fingerprint: str | None = None
            witness_error = str(
                observation.get("_runtime_identity_witness_error") or ""
            ).strip()

            if len(matches) > 1:
                status = "ambiguous_projection_match"
                action = "review_runtime_identity"
                reasons.append("multiple_signed_providers_match_node_port_kind")
                matched_runtime_ids = _runtime_ids(matches)
            elif witness_error and not matches and endpoint_matches:
                status = "runtime_identity_unverified"
                action = "review_runtime_identity"
                reasons.append("runtime_identity_witness_signature_unverified")
                matched_runtime_ids = _runtime_ids(endpoint_matches)
                artifact_identity_reason = "witness_signature_unverified"
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
                elif node_source_match is None:
                    status = "runtime_identity_unverified"
                    action = "review_runtime_identity"
                    reasons.append(
                        "transport_source_ip_not_verifiable_against_signed_access_paths"
                    )
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

                continuity = observation.get("runtime_identity_continuity")
                if witness_error and status == "projected":
                    status = "runtime_identity_unverified"
                    action = "review_runtime_identity"
                    reasons.append("runtime_identity_witness_signature_unverified")
                    artifact_identity_reason = "witness_signature_unverified"
                elif isinstance(verified_witness, dict) and status == "projected":
                    witness_loadout_fingerprint = str(
                        verified_witness.get("loadout_fingerprint") or ""
                    ) or None
                    witness_model_content_sha256 = str(
                        verified_witness.get("model_content_sha256") or ""
                    ) or None
                    witness_signing_key_fingerprint = str(
                        verified_witness.get("witness_signing_key_fingerprint") or ""
                    ) or None
                    witness_node = _node_id(verified_witness.get("node_id"))
                    witness_port = _port(verified_witness.get("runtime_url"))
                    witness_provider_model = str(
                        verified_witness.get("provider_model") or ""
                    ).strip()
                    witness_canary = verified_witness.get("canary")
                    witness_process = verified_witness.get("process")
                    witness_problem: str | None = None
                    if witness_node != node or witness_port != port:
                        witness_problem = "signed_witness_endpoint_identity_mismatch"
                    elif witness_kind != runtime_kind:
                        witness_problem = "signed_witness_runtime_kind_mismatch"
                    elif witness_provider_model.casefold() not in observed_cf:
                        witness_problem = "signed_witness_model_not_observed"
                    elif not isinstance(witness_canary, dict) or witness_canary.get(
                        "rollback_succeeded"
                    ) is not True:
                        witness_problem = "signed_witness_canary_rollback_not_verified"
                    elif not isinstance(witness_process, dict):
                        witness_problem = "signed_witness_process_identity_missing"
                    elif (
                        not isinstance(continuity, dict)
                        or continuity.get("valid") is not True
                        or continuity.get("model_file_valid") is not True
                        or continuity.get("model_process_binding_valid") is not True
                    ):
                        witness_problem = "signed_witness_process_continuity_failed"
                    elif (
                        str(verified_witness.get("model_process_binding") or "")
                        != "proc_maps"
                        or str(continuity.get("model_process_binding") or "")
                        != "proc_maps"
                    ):
                        witness_problem = "signed_witness_model_not_memory_mapped"
                    else:
                        try:
                            continuity_matches = (
                                int(continuity.get("pid") or 0)
                                == int(witness_process.get("pid") or 0)
                                and str(continuity.get("boot_id") or "")
                                == str(witness_process.get("boot_id") or "")
                                and int(continuity.get("process_start_ticks") or 0)
                                == int(witness_process.get("process_start_ticks") or 0)
                                and str(continuity.get("executable_basename") or "")
                                == str(witness_process.get("executable_basename") or "")
                            )
                        except (TypeError, ValueError):
                            continuity_matches = False
                        if not continuity_matches:
                            witness_problem = "signed_witness_process_identity_changed"

                    expected_fingerprints: set[str] = set()
                    for _canonical, ids, fingerprints in groups:
                        if witness_provider_model.casefold() in ids:
                            expected_fingerprints.update(fingerprints)
                    if (
                        witness_problem is None
                        and witness_model_content_sha256 not in expected_fingerprints
                    ):
                        witness_problem = "signed_witness_artifact_fingerprint_mismatch"

                    if witness_problem is None:
                        artifact_identity_verified = True
                        artifact_identity_reason = "signed_model_artifact_and_process_match"
                    elif witness_problem == "signed_witness_artifact_fingerprint_mismatch":
                        status = "model_drift"
                        action = "collect_model_identity_evidence"
                        reasons.append(witness_problem)
                        artifact_identity_reason = witness_problem
                    else:
                        status = "runtime_identity_mismatch"
                        action = "review_runtime_identity"
                        reasons.append(witness_problem)
                        artifact_identity_reason = witness_problem

            items.append(
                {
                    "node_id": node,
                    "source_ip": source_ip or None,
                    "node_source_match": node_source_match,
                    "runtime_observation_id": str(
                        observation.get("runtime_observation_id") or ""
                    ),
                    "observed_runtime_kind": observed_runtime_kind,
                    "runtime_kind": runtime_kind,
                    "runtime_kind_refined_by_signed_witness": (
                        runtime_kind != observed_runtime_kind
                    ),
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
                    "artifact_identity_verified": artifact_identity_verified,
                    "artifact_identity_reason": artifact_identity_reason,
                    "identity_evidence_level": (
                        "signed_model_artifact_and_process"
                        if artifact_identity_verified
                        else "endpoint_and_model_names"
                    ),
                    "witness_loadout_fingerprint": witness_loadout_fingerprint,
                    "witness_model_content_sha256": witness_model_content_sha256,
                    "witness_signing_key_fingerprint": witness_signing_key_fingerprint,
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
        "runtime_identity_unverified",
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


def _verify_projection(
    projection: dict[str, Any],
    verify_key_file: Path,
    expected_key_id: str,
    *,
    now_ms: int | None = None,
) -> None:
    if str(projection.get("schema_version") or "") != "2":
        raise ValueError("operator reconciliation requires schema-v2 Ed25519 projection")
    if str(projection.get("signature_key_id") or "") != expected_key_id:
        raise ValueError("runtime projection signing key id is not the expected operator key")
    src = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(src))
    from assistx.runtime_projection_v2 import verify_projection_v2

    verify_projection_v2(
        projection,
        verify_key_file=str(verify_key_file),
    )
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    try:
        generated_at_ms = int(projection.get("generated_at_ms") or 0)
        expires_at_ms = int(projection.get("expires_at_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime projection timestamps are invalid") from exc
    if generated_at_ms <= 0 or expires_at_ms <= generated_at_ms:
        raise ValueError("runtime projection issuance/expiry timestamps are invalid")
    if expires_at_ms <= now:
        raise ValueError("runtime projection is expired")
    if generated_at_ms > now + 300_000:
        raise ValueError("runtime projection issuance is too far in the future")


def _verify_router_status(
    projection: dict[str, Any],
    router_status: dict[str, Any],
) -> dict[str, Any]:
    if router_status.get("configured") is not True:
        raise ValueError("Auto-Router has no configured runtime projection")
    if router_status.get("fresh") is not True:
        raise ValueError("Auto-Router runtime projection is not fresh")
    current = router_status.get("current")
    if not isinstance(current, dict):
        raise ValueError("Auto-Router current runtime projection status is missing")

    expected = {
        "generation": projection.get("generation"),
        "revision": projection.get("revision"),
        "checksum": projection.get("checksum"),
    }
    actual = {
        "generation": current.get("generation"),
        "revision": current.get("revision"),
        "checksum": current.get("checksum"),
    }
    if actual != expected:
        raise ValueError(
            "Auto-Router current runtime projection does not match the verified projection"
        )
    return {
        "configured": True,
        "fresh": True,
        **actual,
        "expires_at_ms": current.get("expires_at_ms"),
        "applied_at_ms": current.get("applied_at_ms"),
    }


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
    parser.add_argument("--expected-key-id", required=True)
    parser.add_argument(
        "--max-observation-age-seconds",
        type=int,
        default=180,
    )
    parser.add_argument("--router-status", type=Path, required=True)
    parser.add_argument(
        "--runtime-witness-allowed-signers",
        type=Path,
        required=True,
    )
    parser.add_argument("--runtime-witness-identity", required=True)
    parser.add_argument(
        "--runtime-witness-namespace",
        default=_WITNESS_NAMESPACE,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    nodes = _load(args.nodes)
    projection = _load(args.projection)
    router_status = _load(args.router_status)
    _verify_node_witnesses(
        nodes,
        allowed_signers=args.runtime_witness_allowed_signers,
        identity=args.runtime_witness_identity,
        namespace=args.runtime_witness_namespace,
    )
    _verify_projection(
        projection,
        args.verify_key_file,
        args.expected_key_id,
    )
    router_projection = _verify_router_status(projection, router_status)
    result = reconcile(
        nodes,
        projection,
        max_observation_age_seconds=args.max_observation_age_seconds,
        projection_verified=True,
    )
    result["router_projection"] = router_projection
    result["input_sha256"] = {
        "nodes": _sha256_file(args.nodes),
        "projection": _sha256_file(args.projection),
        "router_status": _sha256_file(args.router_status),
        "verify_key": _sha256_file(args.verify_key_file),
        "runtime_witness_allowed_signers": _sha256_file(
            args.runtime_witness_allowed_signers
        ),
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
        f"identity_unverified={summary['runtime_identity_unverified']} "
        f"not_ready={summary['runtime_not_ready']} "
        f"stale={summary['stale_observation']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
