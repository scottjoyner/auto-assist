#!/usr/bin/env python3
"""Research-only detached external witness receipt verifier. Never admits.

The trusted public key pin is intentionally EMPTY. Test-only injected pins
allow offline contract tests; no key creation, signing, network or graph IO.
A valid signature alone is NOT independent host custody/approval.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SCHEMA = "assistx.k2-external-physical-witness.v1"
OBSERVATION_KIND = "x1-k2-local-physical-observation-v1"
NODE = "x1-370"
TRUSTED_WITNESS_KEY_SHA256 = ""  # requires INDEPENDENT operator-controlled pin
# These are NOT learned from a signed observation: operator policy must
# approve exact executable identities as well as the witness signer.
APPROVED_RUNTIME_EXECUTABLE_SHA256 = ""
APPROVED_PROXY_EXECUTABLE_SHA256 = ""
EXPECTED_MODEL_SHA256 = "371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8"
EXPECTED_MODEL_BYTES = 2_159_424_896
EXPECTED_MODEL_ID = "k2-horizon-0.9b"
MAX_BYTES = 64 * 1024
MAX_AGE_MS = 120_000
MAX_LIFETIME_MS = 300_000
NONCE_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
B64_PATTERN = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")
FIELDS = frozenset({
    "schema", "node", "expected_challenge_hex", "issued_at_ms",
    "expires_at_ms", "witness_public_key_b64", "observation", "signature_b64",
})


class WitnessHold(ValueError):
    """Static, non-sensitive refusal code."""


def reject(code: str) -> None:
    raise WitnessHold(code)


def _duplicate_free(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            reject("duplicate_json_key")
        result[key] = value
    return result


def parse_document(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        reject("unsigned_document_size_invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_free,
                           parse_constant=lambda _: reject("nonfinite_json_number"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessHold("invalid_json_encoding") from exc
    if not isinstance(value, dict) or set(value) != FIELDS:
        reject("unexpected_witness_schema_fields")
    return value


def canonical(value: dict) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise WitnessHold("noncanonical_payload") from exc


def b64_strict(encoded: str, *, length: int) -> bytes:
    if (not isinstance(encoded, str) or len(encoded) > 128
            or not B64_PATTERN.fullmatch(encoded)):
        reject("invalid_signature_or_key_encoding")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise WitnessHold("invalid_signature_or_key_encoding") from exc
    if len(data) != length or base64.b64encode(data).decode() != encoded:
        reject("noncanonical_signature_or_key_bytes")
    return data


def _require_true(report: dict, key: str) -> None:
    if report.get(key) is not True:
        reject("missing_verified_" + key)


def validate_observation(obs: Any, *, now_ms: int, issued_ms: int) -> None:
    if not isinstance(obs, dict):
        reject("missing_observation")
    if obs.get("kind") != OBSERVATION_KIND or obs.get("node") != NODE:
        reject("wrong_observation_identity")
    if (obs.get("gate") != "HOLD" or obs.get("can_dispatch") is not False
            or obs.get("operator_approval_verified") is not False
            or obs.get("cryptographic_witness_signature_present") is not False):
        reject("self_admitting_observation_forbidden")
    if obs.get("app_version") != "0.4.26+4":
        reject("unreviewed_runtime_version_claim")
    if obs.get("reasons") != ["independent_signed_witness_and_approval_missing"]:
        reject("internally_contradictory_physical_observation")
    if (obs.get("model_id_expected") != EXPECTED_MODEL_ID
            or obs.get("model_artifact_sha256_observed") != EXPECTED_MODEL_SHA256
            or type(obs.get("model_artifact_bytes_observed")) is not int
            or obs["model_artifact_bytes_observed"] != EXPECTED_MODEL_BYTES):
        reject("model_artifact_identity_not_independently_bound")
    for field in (
        "reviewed_app_version_match",
        "model_artifact_sha256_match",
        "physical_process_socket_chain_verified",
        "model_loaded_physically_verified",
    ):
        _require_true(obs, field)
    try:
        timestamp = obs["collected_at_utc"]
        if not isinstance(timestamp, str):
            raise ValueError("missing timestamp")
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("naive timestamp")
        observed_ms = round(instant.timestamp() * 1000)
    except (KeyError, ValueError, TypeError, OverflowError):
        reject("invalid_observation_timestamp")
    if not 0 <= now_ms - observed_ms <= MAX_AGE_MS:
        reject("observation_expired_or_future")
    if not 0 <= issued_ms - observed_ms <= MAX_AGE_MS:
        reject("observation_not_bound_to_signed_issuance")
    process = obs.get("process")
    if not isinstance(process, dict):
        reject("process_evidence_missing")
    if "collection_error_kind" in process:
        reject("process_evidence_contains_unverified_collection")
    if (not isinstance(process.get("running_executable_sha256"), str)
            or process["running_executable_sha256"]
            != APPROVED_RUNTIME_EXECUTABLE_SHA256):
        reject("runtime_executable_fingerprint_not_approved")
    for field in (
        "executable_target_verified",
        "installed_executable_sha256_match",
        "model_file_mapped_in_process_tree",
    ):
        _require_true(process, field)
    if not isinstance(process.get("boot_id_sha256"), str) or not SHA_PATTERN.fullmatch(
        process["boot_id_sha256"]
    ):
        reject("boot_identity_unverified")
    if (type(process.get("pid")) is not int or process["pid"] < 1
            or type(process.get("start_ticks")) is not int
            or process["start_ticks"] < 1):
        reject("process_start_identity_unverified")
    ownership = process.get("per_transport_socket_owner_verified")
    if not isinstance(ownership, dict) or set(ownership) != {"loopback", "lan", "tailscale"}:
        reject("transport_socket_ownership_incomplete")
    if not all(value is True for value in ownership.values()):
        reject("transport_socket_ownership_unverified")
    proxies = process.get("proxies")
    if not isinstance(proxies, dict) or set(proxies) != {"lan", "tailscale"}:
        reject("transport_proxy_inventory_incomplete")
    for proxy in proxies.values():
        if not isinstance(proxy, dict):
            reject("invalid_transport_proxy")
        for field in ("exact_forward_to_loopback_configured", "listener_inode_owned"):
            _require_true(proxy, field)
        if (type(proxy.get("pid")) is not int or proxy["pid"] < 1
                or type(proxy.get("start_ticks")) is not int or proxy["start_ticks"] < 1
                or not isinstance(proxy.get("running_executable_sha256"), str)
                or not SHA_PATTERN.fullmatch(proxy["running_executable_sha256"])):
            reject("transport_proxy_runtime_identity_unverified")
        if proxy["running_executable_sha256"] != APPROVED_PROXY_EXECUTABLE_SHA256:
            reject("transport_proxy_fingerprint_not_approved")
    paths = obs.get("paths")
    if not isinstance(paths, dict) or set(paths) != {"loopback", "lan", "tailscale"}:
        reject("transport_paths_incomplete")
    for path in paths.values():
        if not isinstance(path, dict):
            reject("invalid_transport_path")
        _require_true(path, "reachable")
        _require_true(path, "expected_model_present")
        if type(path.get("loaded_count")) is not int or path["loaded_count"] < 1:
            reject("model_not_loaded_on_all_paths")


def verify(raw: bytes, *, expected_challenge_hex: str,
           now_ms: int | None = None) -> dict:
    # The pin is not read from the untrusted witness envelope or any CLI
    # argument. Operator policy must provision it independently.
    pin = TRUSTED_WITNESS_KEY_SHA256
    if not isinstance(pin, str) or not SHA_PATTERN.fullmatch(pin):
        reject("no_independent_witness_trust_root")
    if (not isinstance(APPROVED_RUNTIME_EXECUTABLE_SHA256, str)
            or not SHA_PATTERN.fullmatch(APPROVED_RUNTIME_EXECUTABLE_SHA256)
            or not isinstance(APPROVED_PROXY_EXECUTABLE_SHA256, str)
            or not SHA_PATTERN.fullmatch(APPROVED_PROXY_EXECUTABLE_SHA256)):
        reject("independently_approved_binary_fingerprints_missing")
    if not isinstance(expected_challenge_hex, str) or not NONCE_PATTERN.fullmatch(
        expected_challenge_hex
    ):
        reject("missing_independent_fresh_challenge")
    document = parse_document(raw)
    if document["schema"] != SCHEMA or document["node"] != NODE:
        reject("wrong_witness_schema_or_node")
    if document["expected_challenge_hex"] != expected_challenge_hex:
        reject("challenge_mismatch_or_replay")
    now = int(now_ms if now_ms is not None else datetime.now(UTC).timestamp() * 1000)
    issued = document["issued_at_ms"]
    expires = document["expires_at_ms"]
    if type(issued) is not int or type(expires) is not int:
        reject("invalid_witness_time_type")
    if (issued > now or now - issued > MAX_AGE_MS
            or expires <= now or expires <= issued
            or expires - issued > MAX_LIFETIME_MS):
        reject("stale_or_overlong_witness")
    validate_observation(document["observation"], now_ms=now, issued_ms=issued)
    public = b64_strict(document["witness_public_key_b64"], length=32)
    if hashlib.sha256(public).hexdigest() != pin:
        reject("witness_signer_not_independently_approved")
    signature = b64_strict(document["signature_b64"], length=64)
    unsigned = {key: value for key, value in document.items() if key != "signature_b64"}
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise WitnessHold("invalid_witness_signature") from exc
    return {
        "signature_valid": True,
        "observation_claims_complete": True,
        "witness_key_sha256": pin,
        "observation_sha256": hashlib.sha256(
            canonical(document["observation"])).hexdigest(),
        "challenge_bound": True,
        "kind": "k2-physical-witness-offline-verification-research-v1",
        "independent_host_custody_verified": False,
        "operator_approval_verified": False,
        "can_dispatch": False,
        "decision": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-challenge", required=True,
                        help="externally obtained fresh 32-byte lowercase-hex challenge")
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        result = verify(raw, expected_challenge_hex=args.expected_challenge)
        print(json.dumps(result, sort_keys=True))
    except WitnessHold as exc:
        # Codes are static, never echo private raw payloads or key data.
        print(json.dumps({"decision": "HOLD", "can_dispatch": False,
                          "reason_code": str(exc)}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
