from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import urlparse


class RuntimeAdmissionContractError(ValueError):
    """Raised when a desired-state profile or live proof cannot be admitted."""


_UNKNOWN = {"", "unknown", "unresolved", "none", "null", "replace-me"}
_ALLOWED_TRANSPORTS = {
    "loopback",
    "lan",
    "tailscale",
    "host_gateway",
    "local_dns",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _known(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text not in _UNKNOWN and not text.startswith("replace-")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeAdmissionContractError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeAdmissionContractError(f"{label} must be a list")
    return value


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeAdmissionContractError(f"{label} must be an integer") from exc
    if result <= 0:
        raise RuntimeAdmissionContractError(f"{label} must be positive")
    return result


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeAdmissionContractError(f"{label} must be an integer") from exc
    if result < 0:
        raise RuntimeAdmissionContractError(f"{label} must be zero or positive")
    return result


def _nonnegative_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeAdmissionContractError(f"{label} must be numeric") from exc
    if result < 0:
        raise RuntimeAdmissionContractError(f"{label} must be zero or positive")
    return result


def _private_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }:
        return True
    if host.endswith((".lan", ".local", ".internal", ".ts.net")):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        import ipaddress

        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    tailscale = ipaddress.ip_network("100.64.0.0/10")
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address in tailscale
    )


def _runtime_kind_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    text = text.replace(".", "_").replace(" ", "_")
    aliases = {
        "lm_studio": "lmstudio",
        "llama_cpp": "llama_cpp",
        "llamacpp": "llama_cpp",
        "openai_compatible": "openai_compatible",
    }
    return aliases.get(text, text)


def _normalized_sha(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        digest = text.removeprefix("sha256:")
    else:
        digest = text
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeAdmissionContractError(
            f"{label} must be a 64-character SHA-256 fingerprint"
        )
    return "sha256:" + digest


def _require_true(mapping: dict[str, Any], key: str, label: str) -> None:
    if mapping.get(key) is not True:
        raise RuntimeAdmissionContractError(f"{label}.{key} must be true")


def _profile_loadout_fingerprint(profile: dict[str, Any]) -> str:
    desired = _mapping(profile.get("desired"), "profile.desired")
    loadout = _mapping(desired.get("loadout"), "profile.desired.loadout")
    value = loadout.get("loadout_fingerprint") or loadout.get(
        "selection_fingerprint"
    )
    return _normalized_sha(value, "profile desired loadout fingerprint")


def _verify_profile(profile: dict[str, Any]) -> dict[str, Any]:
    schema_version = str(profile.get("schema_version") or "")
    if schema_version not in {
        "fleet_runtime_profile.v2",
        "fleet-runtime-profile.v2",
        "fleet_runtime_profile.canary.v1",
        "fleet-runtime-profile.canary.v1",
    }:
        raise RuntimeAdmissionContractError(
            "profile schema_version is not a supported fleet runtime profile"
        )
    if not _known(profile.get("profile_id")):
        raise RuntimeAdmissionContractError("profile.profile_id is required")
    revision = _positive_int(profile.get("revision"), "profile.revision")

    admission = _mapping(profile.get("admission"), "profile.admission")
    if admission.get("enabled") is not False:
        raise RuntimeAdmissionContractError(
            "profile must remain non-admitted; admission.enabled must be false"
        )

    desired = _mapping(profile.get("desired"), "profile.desired")
    runtime = _mapping(desired.get("runtime"), "profile.desired.runtime")
    model = _mapping(desired.get("model"), "profile.desired.model")
    capacity = _mapping(desired.get("capacity"), "profile.desired.capacity")
    loadout = _mapping(desired.get("loadout"), "profile.desired.loadout")
    evidence = _mapping(profile.get("evidence"), "profile.evidence")
    gates = _mapping(evidence.get("gates"), "profile.evidence.gates")
    canary = _mapping(
        evidence.get("runtime_canary"),
        "profile.evidence.runtime_canary",
    )
    rollback = _mapping(profile.get("rollback"), "profile.rollback")
    node = _mapping(profile.get("node"), "profile.node")

    for gate in (
        "exact_loadout_qualification",
        "hermes_intelligence",
        "hermes_context_pressure",
        "authenticated_qualification_run",
        "runtime_canary_soak",
        "runtime_canary_rollback",
        "runtime_canary_attested",
    ):
        _require_true(gates, gate, "profile.evidence.gates")
    _require_true(canary, "success", "profile.evidence.runtime_canary")
    _require_true(
        canary,
        "rollback_succeeded",
        "profile.evidence.runtime_canary",
    )
    _require_true(
        canary,
        "non_admitting",
        "profile.evidence.runtime_canary",
    )

    loadout_fingerprint = _profile_loadout_fingerprint(profile)
    qualification = _mapping(
        evidence.get("qualification"),
        "profile.evidence.qualification",
    )
    qualification_fp = _normalized_sha(
        qualification.get("loadout_fingerprint"),
        "qualification loadout fingerprint",
    )
    canary_fp = _normalized_sha(
        canary.get("loadout_fingerprint"),
        "runtime canary loadout fingerprint",
    )
    if len({loadout_fingerprint, qualification_fp, canary_fp}) != 1:
        raise RuntimeAdmissionContractError(
            "profile, qualification, and canary loadout fingerprints differ"
        )

    model_fingerprint = model.get("artifact_fingerprint") or model.get("sha256")
    model_fingerprint = _normalized_sha(
        model_fingerprint,
        "profile model artifact fingerprint",
    )
    if not _known(runtime.get("physical_instance")):
        raise RuntimeAdmissionContractError(
            "profile desired runtime physical_instance is required"
        )
    if not _known(runtime.get("kind")):
        raise RuntimeAdmissionContractError(
            "profile desired runtime kind is required"
        )
    if not _known(model.get("id")):
        raise RuntimeAdmissionContractError("profile desired model id is required")
    if not _known(model.get("quantization")):
        raise RuntimeAdmissionContractError(
            "profile desired model quantization is required"
        )
    _positive_int(
        capacity.get("parallel_slots"),
        "profile desired capacity parallel_slots",
    )
    context_length = (
        capacity.get("max_context_tokens")
        or _mapping(loadout.get("context"), "profile.desired.loadout.context").get(
            "configured_tokens"
        )
    )
    _positive_int(context_length, "profile desired context length")
    if not _known(rollback.get("profile_id")):
        raise RuntimeAdmissionContractError(
            "profile rollback.profile_id is required"
        )
    if not _list(rollback.get("procedure"), "profile.rollback.procedure"):
        raise RuntimeAdmissionContractError(
            "profile.rollback.procedure must not be empty"
        )

    return {
        "profile_id": str(profile["profile_id"]),
        "revision": revision,
        "node_id": str(node.get("node_id") or node.get("id") or "").strip(),
        "physical_instance": str(runtime["physical_instance"]),
        "runtime_kind": str(runtime["kind"]),
        "runtime_backend": str(runtime.get("backend") or ""),
        "model_id": str(model["id"]),
        "model_fingerprint": model_fingerprint,
        "quantization": str(model["quantization"]),
        "context_length": int(context_length),
        "parallel_slots": int(capacity["parallel_slots"]),
        "loadout_fingerprint": loadout_fingerprint,
        "rollback_profile": str(rollback["profile_id"]),
        "bundle_fingerprint": str(evidence.get("bundle_fingerprint") or ""),
        "qualification_fingerprint": str(
            evidence.get("qualification_fingerprint")
            or qualification.get("fingerprint")
            or ""
        ),
        "qualification_attestation_fingerprint": str(
            evidence.get("qualification_run_attestation_fingerprint") or ""
        ),
        "canary_manifest_fingerprint": str(
            canary.get("manifest_fingerprint") or ""
        ),
        "canary_attestation_fingerprint": str(
            canary.get("attestation_fingerprint") or ""
        ),
        "canary_signer_identity": str(canary.get("signer_identity") or ""),
        "canary_signing_key_fingerprint": str(
            canary.get("signing_key_fingerprint") or ""
        ),
    }


def _verify_live(
    live: dict[str, Any],
    profile: dict[str, Any],
    *,
    now_ms: int,
) -> dict[str, Any]:
    if str(live.get("schema_version") or "") != "assistx.live-runtime-proof.v1":
        raise RuntimeAdmissionContractError(
            "live proof schema_version must equal assistx.live-runtime-proof.v1"
        )
    observed_at_ms = _positive_int(
        live.get("observed_at_ms"),
        "live.observed_at_ms",
    )
    expires_at_ms = _positive_int(
        live.get("expires_at_ms"),
        "live.expires_at_ms",
    )
    if observed_at_ms > now_ms:
        raise RuntimeAdmissionContractError(
            "live proof observation cannot be in the future"
        )
    if expires_at_ms <= now_ms:
        raise RuntimeAdmissionContractError("live proof is expired")
    if expires_at_ms <= observed_at_ms:
        raise RuntimeAdmissionContractError(
            "live proof expiry must be after observation"
        )

    for field in (
        "node_id",
        "runtime_instance_id",
        "runtime_kind",
        "runtime_version",
        "process_id",
        "model_instance_id",
        "model_key",
        "provider_model",
        "quantization",
    ):
        if not _known(live.get(field)):
            raise RuntimeAdmissionContractError(f"live.{field} is required")

    if str(live["node_id"]) != profile["node_id"]:
        raise RuntimeAdmissionContractError(
            "live node identity does not match the profile"
        )
    if str(live["runtime_instance_id"]) != profile["physical_instance"]:
        raise RuntimeAdmissionContractError(
            "live runtime identity does not match profile physical_instance"
        )
    if _runtime_kind_key(live["runtime_kind"]) != _runtime_kind_key(
        profile["runtime_kind"]
    ):
        raise RuntimeAdmissionContractError(
            "live runtime kind does not match the profile"
        )

    artifact_fingerprint = _normalized_sha(
        live.get("artifact_fingerprint"),
        "live artifact_fingerprint",
    )
    if artifact_fingerprint != profile["model_fingerprint"]:
        raise RuntimeAdmissionContractError(
            "live model artifact fingerprint does not match the profile"
        )
    if str(live["quantization"]) != profile["quantization"]:
        raise RuntimeAdmissionContractError(
            "live quantization does not match the profile"
        )
    context_length = _positive_int(
        live.get("context_length"),
        "live.context_length",
    )
    if context_length != profile["context_length"]:
        raise RuntimeAdmissionContractError(
            "live context length does not match the profile"
        )

    capacity = _mapping(live.get("capacity"), "live.capacity")
    slots = _positive_int(
        capacity.get("parallel_slots"),
        "live.capacity.parallel_slots",
    )
    if slots != profile["parallel_slots"]:
        raise RuntimeAdmissionContractError(
            "live parallel slot capacity does not match the profile"
        )
    queue_limit = _nonnegative_int(
        capacity.get("queue_limit"),
        "live.capacity.queue_limit",
    )
    queue_timeout = _nonnegative_float(
        capacity.get("queue_timeout_seconds"),
        "live.capacity.queue_timeout_seconds",
    )
    if not _known(capacity.get("shared_capacity_key")):
        raise RuntimeAdmissionContractError(
            "live.capacity.shared_capacity_key is required"
        )

    paths = _list(live.get("access_paths"), "live.access_paths")
    if not paths:
        raise RuntimeAdmissionContractError(
            "live.access_paths must contain at least one private path"
        )
    normalized_paths: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    preferences: set[int] = set()
    for index, item in enumerate(paths):
        path = _mapping(item, f"live.access_paths[{index}]")
        base_url = str(path.get("base_url") or path.get("url") or "").rstrip("/")
        transport = str(path.get("transport") or path.get("kind") or "").lower()
        preference = _nonnegative_int(
            path.get("preference", path.get("priority")),
            f"live.access_paths[{index}].preference",
        )
        if transport not in _ALLOWED_TRANSPORTS:
            raise RuntimeAdmissionContractError(
                f"live.access_paths[{index}].transport is unsupported"
            )
        if not _private_url(base_url):
            raise RuntimeAdmissionContractError(
                f"live.access_paths[{index}].base_url is not private"
            )
        if base_url in seen_urls:
            raise RuntimeAdmissionContractError("live access path URL is duplicated")
        if preference in preferences:
            raise RuntimeAdmissionContractError(
                "live access path preference is duplicated"
            )
        seen_urls.add(base_url)
        preferences.add(preference)
        normalized_paths.append(
            {
                "base_url": base_url,
                "transport": transport,
                "preference": preference,
                "evidence_ref": str(
                    path.get("evidence_ref")
                    or f"live-proof:{fingerprint(path)}"
                ),
                "evidence_sha256": str(
                    path.get("evidence_sha256") or fingerprint(path)
                ),
            }
        )
    normalized_paths.sort(key=lambda item: item["preference"])

    rollback = _mapping(live.get("rollback"), "live.rollback")
    _require_true(rollback, "health_verified", "live.rollback")
    _require_true(rollback, "boot_recovery_verified", "live.rollback")
    if str(rollback.get("profile_id") or "") != profile["rollback_profile"]:
        raise RuntimeAdmissionContractError(
            "live rollback profile does not match the desired profile"
        )

    return {
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": expires_at_ms,
        "node_id": str(live["node_id"]),
        "runtime_instance_id": str(live["runtime_instance_id"]),
        "runtime_kind": _runtime_kind_key(live["runtime_kind"]),
        "runtime_version": str(live["runtime_version"]),
        "headless": bool(live.get("headless")),
        "process_id": str(live["process_id"]),
        "model_instance_id": str(live["model_instance_id"]),
        "model_key": str(live["model_key"]),
        "provider_model": str(live["provider_model"]),
        "artifact_fingerprint": artifact_fingerprint,
        "quantization": str(live["quantization"]),
        "context_length": context_length,
        "capabilities": sorted(
            {
                str(item)
                for item in _list(live.get("capabilities"), "live.capabilities")
                if str(item).strip()
            }
            | {"local_only"}
        ),
        "capacity": {
            "parallel_slots": slots,
            "queue_limit": queue_limit,
            "queue_timeout_seconds": queue_timeout,
            "shared_capacity_key": str(capacity["shared_capacity_key"]),
            "evidence_ref": str(
                capacity.get("evidence_ref")
                or f"live-proof:{fingerprint(capacity)}"
            ),
            "evidence_sha256": str(
                capacity.get("evidence_sha256") or fingerprint(capacity)
            ),
        },
        "access_paths": normalized_paths,
        "rollback": {
            "profile_id": str(rollback["profile_id"]),
            "health_verified": True,
            "boot_recovery_verified": True,
            "evidence_ref": str(
                rollback.get("evidence_ref")
                or f"live-proof:{fingerprint(rollback)}"
            ),
            "evidence_sha256": str(
                rollback.get("evidence_sha256") or fingerprint(rollback)
            ),
        },
    }


def build_runtime_admission_candidate(
    profile: dict[str, Any],
    live_proof: dict[str, Any],
    *,
    generation: int,
    expected_current_generation: int,
    approved_by: str,
    approval_id: str,
    ttl_seconds: int = 300,
    now_ms: int | None = None,
    require_lan_and_tailscale: bool = True,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    generation_value = _positive_int(generation, "generation")
    expected = _nonnegative_int(
        expected_current_generation,
        "expected_current_generation",
    )
    if generation_value != expected + 1:
        raise RuntimeAdmissionContractError(
            "generation must equal expected_current_generation + 1"
        )
    ttl = _positive_int(ttl_seconds, "ttl_seconds")
    if ttl < 30 or ttl > 900:
        raise RuntimeAdmissionContractError(
            "ttl_seconds must be between 30 and 900"
        )
    if not _known(approved_by) or not _known(approval_id):
        raise RuntimeAdmissionContractError(
            "approved_by and approval_id are required"
        )

    verified_profile = _verify_profile(profile)
    if not verified_profile["node_id"]:
        raise RuntimeAdmissionContractError("profile node identity is required")
    verified_live = _verify_live(
        live_proof,
        verified_profile,
        now_ms=now,
    )
    transports = {
        item["transport"] for item in verified_live["access_paths"]
    }
    if require_lan_and_tailscale and not {"lan", "tailscale"}.issubset(transports):
        raise RuntimeAdmissionContractError(
            "live proof must contain both LAN and Tailscale paths"
        )
    if require_lan_and_tailscale:
        first_transport = verified_live["access_paths"][0]["transport"]
        if first_transport != "lan":
            raise RuntimeAdmissionContractError(
                "LAN must be the preferred access path"
            )

    profile_fingerprint = fingerprint(profile)
    live_fingerprint = fingerprint(live_proof)
    candidate_core = {
        "schema_version": "assistx.runtime-admission-candidate.v1",
        "created_at_ms": now,
        "profile": {
            "profile_id": verified_profile["profile_id"],
            "revision": verified_profile["revision"],
            "fingerprint": profile_fingerprint,
            "admission_enabled": False,
        },
        "loadout_fingerprint": verified_profile["loadout_fingerprint"],
        "evidence": {
            "bundle_fingerprint": verified_profile["bundle_fingerprint"],
            "qualification_fingerprint": verified_profile[
                "qualification_fingerprint"
            ],
            "qualification_attestation_fingerprint": verified_profile[
                "qualification_attestation_fingerprint"
            ],
            "canary_manifest_fingerprint": verified_profile[
                "canary_manifest_fingerprint"
            ],
            "canary_attestation_fingerprint": verified_profile[
                "canary_attestation_fingerprint"
            ],
            "canary_signer_identity": verified_profile[
                "canary_signer_identity"
            ],
            "canary_signing_key_fingerprint": verified_profile[
                "canary_signing_key_fingerprint"
            ],
            "live_proof_fingerprint": live_fingerprint,
            "live_proof_observed_at_ms": verified_live["observed_at_ms"],
            "live_proof_expires_at_ms": verified_live["expires_at_ms"],
        },
        "runtime": verified_live,
        "approval": {
            "generation": generation_value,
            "expected_current_generation": expected,
            "approved_by": approved_by,
            "approval_id": approval_id,
            "ttl_seconds": ttl,
            "require_lan_and_tailscale": bool(require_lan_and_tailscale),
        },
    }
    candidate_fingerprint = fingerprint(candidate_core)
    candidate_id = (
        f"runtime-admission:{verified_profile['profile_id']}:"
        f"{verified_profile['revision']}:"
        f"{candidate_fingerprint.removeprefix('sha256:')[:16]}"
    )
    lease_expires_at_ms = min(
        now + ttl * 1000,
        verified_live["expires_at_ms"],
    )
    projection_ttl = max(1, (lease_expires_at_ms - now) // 1000)
    if projection_ttl < 30:
        raise RuntimeAdmissionContractError(
            "live proof does not remain fresh for the minimum 30-second lease"
        )

    manifest = {
        "schema_version": 1,
        "generation": generation_value,
        "expected_current_generation": expected,
        "revision": (
            f"profile:{verified_profile['profile_id']}:"
            f"{verified_profile['revision']}:"
            f"{candidate_fingerprint.removeprefix('sha256:')[:16]}"
        ),
        "approved_by": approved_by,
        "approval_id": approval_id,
        "ttl_seconds": int(projection_ttl),
        "require_lan_and_tailscale": bool(require_lan_and_tailscale),
        "admission_candidate_id": candidate_id,
        "admission_candidate_fingerprint": candidate_fingerprint,
        "profile_id": verified_profile["profile_id"],
        "profile_revision": verified_profile["revision"],
        "profile_fingerprint": profile_fingerprint,
        "loadout_fingerprint": verified_profile["loadout_fingerprint"],
        "live_proof_fingerprint": live_fingerprint,
        "runtimes": [
            {
                "runtime_instance_id": verified_live[
                    "runtime_instance_id"
                ],
                "node_id": verified_live["node_id"],
                "runtime_kind": verified_live["runtime_kind"],
                "runtime_version": verified_live["runtime_version"],
                "headless": verified_live["headless"],
                "process_id": verified_live["process_id"],
                "shared_capacity_key": verified_live["capacity"][
                    "shared_capacity_key"
                ],
                "rollback": verified_live["rollback"],
                "capacity": {
                    key: value
                    for key, value in verified_live["capacity"].items()
                    if key != "shared_capacity_key"
                },
                "access_paths": verified_live["access_paths"],
                "models": [
                    {
                        "model_instance_id": verified_live[
                            "model_instance_id"
                        ],
                        "model_key": verified_live["model_key"],
                        "provider_model": verified_live[
                            "provider_model"
                        ],
                        "artifact_fingerprint": verified_live[
                            "artifact_fingerprint"
                        ],
                        "quantization": verified_live["quantization"],
                        "context_length": verified_live[
                            "context_length"
                        ],
                        "capabilities": verified_live["capabilities"],
                        "evidence_ref": (
                            "profile:"
                            + profile_fingerprint
                            + ":model"
                        ),
                        "evidence_sha256": verified_live[
                            "artifact_fingerprint"
                        ],
                    }
                ],
            }
        ],
    }
    return {
        **candidate_core,
        "candidate_id": candidate_id,
        "candidate_fingerprint": candidate_fingerprint,
        "lease": {
            "schema_version": "assistx.runtime-admission-lease.v1",
            "lease_id": (
                f"runtime-admission-lease:{generation_value}:"
                f"{candidate_fingerprint.removeprefix('sha256:')[:16]}"
            ),
            "state": "ACTIVE",
            "issued_at_ms": now,
            "expires_at_ms": lease_expires_at_ms,
            "candidate_id": candidate_id,
            "candidate_fingerprint": candidate_fingerprint,
            "generation": generation_value,
            "approval_id": approval_id,
            "approved_by": approved_by,
            "revocable": True,
        },
        "projection_manifest": manifest,
    }


def validate_runtime_admission_candidate(
    candidate: dict[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if str(candidate.get("schema_version") or "") != (
        "assistx.runtime-admission-candidate.v1"
    ):
        raise RuntimeAdmissionContractError(
            "candidate schema_version is invalid"
        )
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    received = str(candidate.get("candidate_fingerprint") or "")
    core = {
        key: value
        for key, value in candidate.items()
        if key
        not in {
            "candidate_id",
            "candidate_fingerprint",
            "lease",
            "projection_manifest",
        }
    }
    expected = fingerprint(core)
    if received != expected:
        raise RuntimeAdmissionContractError(
            "candidate fingerprint mismatch"
        )
    lease = _mapping(candidate.get("lease"), "candidate.lease")
    if lease.get("state") != "ACTIVE":
        raise RuntimeAdmissionContractError(
            "candidate lease must be ACTIVE"
        )
    if int(lease.get("expires_at_ms") or 0) <= now:
        raise RuntimeAdmissionContractError("candidate lease is expired")
    if lease.get("candidate_fingerprint") != received:
        raise RuntimeAdmissionContractError(
            "candidate lease fingerprint mismatch"
        )
    manifest = _mapping(
        candidate.get("projection_manifest"),
        "candidate.projection_manifest",
    )
    if manifest.get("admission_candidate_fingerprint") != received:
        raise RuntimeAdmissionContractError(
            "projection manifest candidate fingerprint mismatch"
        )
    if manifest.get("admission_candidate_id") != candidate.get(
        "candidate_id"
    ):
        raise RuntimeAdmissionContractError(
            "projection manifest candidate identity mismatch"
        )
    if manifest.get("profile_fingerprint") != _mapping(
        candidate.get("profile"),
        "candidate.profile",
    ).get("fingerprint"):
        raise RuntimeAdmissionContractError(
            "projection manifest profile fingerprint mismatch"
        )
    return candidate
