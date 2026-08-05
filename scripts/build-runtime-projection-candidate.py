#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

import yaml


_UNKNOWN = {"", "unknown", "unresolved", "none", "null", "replace-me"}
_REQUIRED_EXTERNAL_GATES = {
    "physical_runtime_identity",
    "model_artifact_fingerprint",
    "container_path_reachability",
    "lan_preference_and_tailscale_fallback",
    "shared_slot_admission",
    "rollback_canary",
}
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


class ProfileCandidateError(ValueError):
    pass


def _known(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text not in _UNKNOWN and not text.startswith("replace-")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileCandidateError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ProfileCandidateError(f"{label} must be a non-empty list")
    return value


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _parse_utc(value: Any, label: str) -> datetime:
    if not _known(value):
        raise ProfileCandidateError(f"{label} is required")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProfileCandidateError(
            f"{label} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ProfileCandidateError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _runtime_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    runtime_kind = _RUNTIME_KIND_ALIASES.get(normalized)
    if runtime_kind is None:
        raise ProfileCandidateError(f"unsupported runtime kind {value!r}")
    return runtime_kind


def _require_canary(profile: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    admission = _mapping(profile.get("admission"), "admission")
    if admission.get("enabled") is not False:
        raise ProfileCandidateError(
            "source profile must remain non-admitting until an operator issues the lease"
        )
    required_gates = {
        str(item)
        for item in _sequence(
            admission.get("required_external_gates"),
            "admission.required_external_gates",
        )
    }
    missing_external = sorted(_REQUIRED_EXTERNAL_GATES - required_gates)
    if missing_external:
        raise ProfileCandidateError(
            "source profile is missing required external gates: "
            + ", ".join(missing_external)
        )

    observation = _mapping(profile.get("observation"), "observation")
    expires_at = _parse_utc(
        observation.get("expires_at_utc"),
        "observation.expires_at_utc",
    )
    if expires_at <= now:
        raise ProfileCandidateError("source profile observation is expired")

    evidence = _mapping(profile.get("evidence"), "evidence")
    canary = _mapping(evidence.get("runtime_canary"), "evidence.runtime_canary")
    if canary.get("success") is not True:
        raise ProfileCandidateError("runtime canary did not succeed")
    if canary.get("rollback_succeeded") is not True:
        raise ProfileCandidateError("runtime canary rollback did not succeed")
    soak = _mapping(canary.get("soak"), "evidence.runtime_canary.soak")
    if soak.get("passed") is not True:
        raise ProfileCandidateError("runtime canary soak did not pass")
    canary_admission = _mapping(
        canary.get("admission"),
        "evidence.runtime_canary.admission",
    )
    if canary_admission.get("admitted") is not False:
        raise ProfileCandidateError(
            "runtime canary evidence must remain explicitly non-admitting"
        )

    gates = _mapping(evidence.get("gates"), "evidence.gates")
    for gate in (
        "runtime_canary_soak",
        "runtime_canary_rollback",
        "runtime_canary_attested",
    ):
        if gates.get(gate) is not True:
            raise ProfileCandidateError(f"evidence gate {gate} is not satisfied")

    for field in (
        "manifest_fingerprint",
        "attestation_fingerprint",
        "signer_identity",
        "signing_key_fingerprint",
    ):
        if not _known(canary.get(field)):
            raise ProfileCandidateError(
                f"evidence.runtime_canary.{field} must be resolved"
            )
    return canary


def build_candidate(
    profile: dict[str, Any],
    *,
    generation: int,
    expected_current_generation: int,
    approved_by: str,
    approval_id: str,
    process_id: str,
    runtime_version: str | None = None,
    provider_model: str | None = None,
    ttl_seconds: int = 300,
    queue_limit: int = 4,
    queue_timeout_seconds: float = 30,
    capabilities: list[str] | None = None,
    require_lan_and_tailscale: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ProfileCandidateError("profile must be a JSON object")
    if generation != expected_current_generation + 1:
        raise ProfileCandidateError(
            "generation must equal expected_current_generation + 1"
        )
    if not _known(approved_by) or not _known(approval_id):
        raise ProfileCandidateError("approved_by and approval_id are required")
    if not _known(process_id):
        raise ProfileCandidateError(
            "process_id must be resolved from the live runtime"
        )
    if ttl_seconds < 30 or ttl_seconds > 900:
        raise ProfileCandidateError("ttl_seconds must be between 30 and 900")
    if queue_limit < 0 or queue_timeout_seconds < 0:
        raise ProfileCandidateError(
            "queue limits and timeouts must be zero or positive"
        )

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    canary = _require_canary(profile, now=current_time)

    profile_id = profile.get("profile_id")
    profile_revision = profile.get("revision")
    if not _known(profile_id) or not _known(profile_revision):
        raise ProfileCandidateError("profile_id and revision must be resolved")

    node = _mapping(profile.get("node"), "node")
    node_id = node.get("node_id")
    if not _known(node_id):
        raise ProfileCandidateError("node.node_id must be resolved")

    desired = _mapping(profile.get("desired"), "desired")
    runtime = _mapping(desired.get("runtime"), "desired.runtime")
    model = _mapping(desired.get("model"), "desired.model")
    capacity = _mapping(desired.get("capacity"), "desired.capacity")

    runtime_instance_id = runtime.get("physical_instance")
    if not _known(runtime_instance_id):
        raise ProfileCandidateError(
            "desired.runtime.physical_instance must be resolved"
        )
    normalized_runtime_kind = _runtime_kind(runtime.get("kind"))
    resolved_runtime_version = runtime_version or runtime.get("engine_version")
    if not _known(resolved_runtime_version):
        raise ProfileCandidateError(
            "runtime_version must be supplied or present as desired.runtime.engine_version"
        )

    access_paths = _sequence(
        runtime.get("access_paths"),
        "desired.runtime.access_paths",
    )
    projected_paths: list[dict[str, Any]] = []
    transports: set[str] = set()
    seen_urls: set[str] = set()
    observation = _mapping(profile.get("observation"), "observation")
    path_evidence = observation.get("artifact")
    if not _known(path_evidence):
        raise ProfileCandidateError("observation.artifact must be resolved")
    for index, item in enumerate(access_paths):
        path = _mapping(item, f"desired.runtime.access_paths[{index}]")
        url = str(path.get("url") or "").strip().rstrip("/")
        transport = str(path.get("transport") or "").strip().lower()
        try:
            preference = int(path.get("priority"))
        except (TypeError, ValueError) as exc:
            raise ProfileCandidateError(
                f"desired.runtime.access_paths[{index}].priority is invalid"
            ) from exc
        if not _known(url) or not _known(transport):
            raise ProfileCandidateError(
                f"desired.runtime.access_paths[{index}] is incomplete"
            )
        if url in seen_urls:
            raise ProfileCandidateError(f"duplicate access path URL {url!r}")
        seen_urls.add(url)
        transports.add(transport)
        projected_paths.append(
            {
                "base_url": url,
                "transport": transport,
                "preference": preference,
                "evidence_ref": str(path_evidence),
            }
        )
    projected_paths.sort(key=lambda item: item["preference"])
    if require_lan_and_tailscale:
        if not {"lan", "tailscale"}.issubset(transports):
            raise ProfileCandidateError(
                "profile must contain both LAN and Tailscale access paths"
            )
        if projected_paths[0]["transport"] != "lan":
            raise ProfileCandidateError(
                "LAN must be preferred before fallback transports"
            )

    try:
        parallel_slots = int(capacity.get("parallel_slots"))
        context_length = int(capacity.get("max_context_tokens"))
    except (TypeError, ValueError) as exc:
        raise ProfileCandidateError(
            "capacity.parallel_slots and max_context_tokens must be integers"
        ) from exc
    if parallel_slots <= 0 or context_length <= 0:
        raise ProfileCandidateError(
            "capacity.parallel_slots and max_context_tokens must be positive"
        )

    model_id = model.get("id")
    artifact_fingerprint = model.get("artifact_fingerprint")
    quantization = model.get("quantization")
    for field, value in (
        ("desired.model.id", model_id),
        ("desired.model.artifact_fingerprint", artifact_fingerprint),
        ("desired.model.quantization", quantization),
    ):
        if not _known(value):
            raise ProfileCandidateError(f"{field} must be resolved")
    resolved_provider_model = provider_model or str(model_id)
    if not _known(resolved_provider_model):
        raise ProfileCandidateError("provider_model must be resolved")

    evidence = _mapping(profile.get("evidence"), "evidence")
    artifacts = _mapping(evidence.get("artifacts"), "evidence.artifacts")
    capacity_evidence = artifacts.get("reliability")
    model_evidence = artifacts.get("model_inventory")
    if not _known(capacity_evidence) or not _known(model_evidence):
        raise ProfileCandidateError(
            "reliability and model inventory evidence artifacts are required"
        )

    capability_set = {
        str(item).strip()
        for item in (capabilities or ["chat", "streaming", "local_only"])
        if str(item).strip()
    }
    capability_set.add("local_only")
    model_instance_seed = (
        f"{profile_id}|{runtime_instance_id}|{artifact_fingerprint}|"
        f"{resolved_provider_model}"
    )
    model_instance_id = (
        "profile-model-"
        + hashlib.sha256(model_instance_seed.encode("utf-8")).hexdigest()[:24]
    )
    profile_fingerprint = _canonical_sha256(profile)

    return {
        "schema_version": 1,
        "generation": int(generation),
        "expected_current_generation": int(expected_current_generation),
        "revision": f"{profile_id}@{profile_revision}",
        "approved_by": str(approved_by),
        "approval_id": str(approval_id),
        "ttl_seconds": int(ttl_seconds),
        "require_lan_and_tailscale": bool(require_lan_and_tailscale),
        "source_profile": {
            "profile_id": str(profile_id),
            "revision": profile_revision,
            "fingerprint": profile_fingerprint,
            "canary_manifest_fingerprint": canary["manifest_fingerprint"],
            "canary_attestation_fingerprint": canary[
                "attestation_fingerprint"
            ],
            "canary_signer_identity": canary["signer_identity"],
            "canary_signing_key_fingerprint": canary[
                "signing_key_fingerprint"
            ],
            "non_admitting_source": True,
        },
        "runtimes": [
            {
                "runtime_instance_id": str(runtime_instance_id),
                "node_id": str(node_id),
                "runtime_kind": normalized_runtime_kind,
                "runtime_version": str(resolved_runtime_version),
                "headless": bool(runtime.get("headless", True)),
                "process_id": str(process_id),
                "capacity": {
                    "parallel_slots": parallel_slots,
                    "queue_limit": int(queue_limit),
                    "queue_timeout_seconds": float(queue_timeout_seconds),
                    "evidence_ref": str(capacity_evidence),
                },
                "access_paths": projected_paths,
                "models": [
                    {
                        "model_instance_id": model_instance_id,
                        "model_key": str(model_id),
                        "provider_model": str(resolved_provider_model),
                        "artifact_fingerprint": str(artifact_fingerprint),
                        "quantization": str(quantization),
                        "context_length": context_length,
                        "capabilities": sorted(capability_set),
                        "evidence_ref": str(model_evidence),
                    }
                ],
            }
        ],
    }


def _load_profile(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileCandidateError(f"unable to read profile: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileCandidateError(f"profile is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileCandidateError("profile must be a JSON object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-mutating AssistX runtime projection approval candidate "
            "from an attested fleet runtime profile."
        )
    )
    parser.add_argument("profile", type=pathlib.Path)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--expected-current-generation", type=int, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--process-id", required=True)
    parser.add_argument("--runtime-version")
    parser.add_argument("--provider-model")
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument("--queue-limit", type=int, default=4)
    parser.add_argument("--queue-timeout-seconds", type=float, default=30)
    parser.add_argument("--capability", action="append", dest="capabilities")
    parser.add_argument(
        "--allow-non-dual-path",
        action="store_true",
        help=(
            "Permit a candidate without both LAN and Tailscale paths. "
            "Production admission should normally retain the dual-path gate."
        ),
    )
    parser.add_argument("--format", choices=("yaml", "json"), default="yaml")
    parser.add_argument("--output", type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = _load_profile(args.profile)
        candidate = build_candidate(
            profile,
            generation=args.generation,
            expected_current_generation=args.expected_current_generation,
            approved_by=args.approved_by,
            approval_id=args.approval_id,
            process_id=args.process_id,
            runtime_version=args.runtime_version,
            provider_model=args.provider_model,
            ttl_seconds=args.ttl_seconds,
            queue_limit=args.queue_limit,
            queue_timeout_seconds=args.queue_timeout_seconds,
            capabilities=args.capabilities,
            require_lan_and_tailscale=not args.allow_non_dual_path,
        )
    except ProfileCandidateError as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        rendered = json.dumps(candidate, indent=2, sort_keys=True) + "\n"
    else:
        rendered = yaml.safe_dump(candidate, sort_keys=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
