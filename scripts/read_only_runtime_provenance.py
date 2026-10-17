"""Inspect deployed fleet image/identity custody without secrets or mutations.

This diagnostic MUST NOT establish source-to-image provenance from a mutable
Docker tag or unsigned OCI label. Source binding remains UNVERIFIED until
an independently trusted, signed build attestation is checked elsewhere.

No Docker exec, Bolt, SQL, container restart, network mutation or deployment.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

CONTAINERS = (
    "assistx-api",
    "assistx-worker",
    "assistx-hermes-adapter",
    "neo4j",
    "git-router-1",
)
GRAPH_WORKLOADS = frozenset(CONTAINERS[:3])
IMMUTABLE_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")


class EvidenceHold(RuntimeError):
    """The diagnostic cannot establish adequate read-only identity evidence."""


def _docker_inspect(name: str) -> dict[str, Any]:
    if name not in CONTAINERS:
        raise EvidenceHold("container outside fixed evidence allowlist")
    try:
        raw = subprocess.check_output(
            ["docker", "container", "inspect", name],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        )
        values = json.loads(raw)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, TypeError) as exc:
        raise EvidenceHold(f"container inspect unavailable for {name}") from exc
    if not isinstance(values, list) or len(values) != 1:
        raise EvidenceHold(f"ambiguous inspect response for {name}")
    value = values[0]
    if value.get("Name") != "/" + name:
        raise EvidenceHold("unexpected container identity")
    return value


def _image_claim(img: dict[str, Any]) -> dict[str, Any]:
    immutable = img.get("Image", "")
    if not isinstance(immutable, str) or not IMMUTABLE_IMAGE_ID.fullmatch(immutable):
        raise EvidenceHold("container image lacks immutable sha256 ID")
    labels = (img.get("Config") or {}).get("Labels") or {}
    revision = labels.get("org.opencontainers.image.revision")
    if revision is not None and (not isinstance(revision, str)
                                 or not REVISION.fullmatch(revision)):
        revision = None
    return {
        "image_id": immutable,
        "claimed_source_sha": revision,
        "source_to_image_verified": False,
        "source_binding": "UNVERIFIED_UNSIGNED_RUNTIME_CLAIM",
    }


def _env(items: Any) -> dict[str, str]:
    if not isinstance(items, list):
        raise EvidenceHold("missing configuration environment inventory")
    values = {}
    for item in items:
        if not isinstance(item, str) or "=" not in item:
            raise EvidenceHold("malformed environment inventory")
        key, value = item.split("=", 1)
        if key in ("NEO4J_USER", "NEO4J_URI"):
            if key in values:
                raise EvidenceHold("duplicate graph identity configuration")
            values[key] = value
    return values


def _groups(entries: dict[str, dict[str, str]], key: str) -> dict[str, str]:
    """Session-local aliases; never reveal/hash actual usernames or endpoints."""
    groups: dict[str, str] = {}
    lookup: dict[str, str] = {}
    for name in GRAPH_WORKLOADS:
        value = entries.get(name, {}).get(key)
        if not value:
            groups[name] = "UNKNOWN"
            continue
        if value not in lookup:
            lookup[value] = "G" + str(len(lookup) + 1)
        groups[name] = lookup[value]
    return groups


def build_report(objects: dict[str, dict[str, Any]], now: str) -> dict[str, Any]:
    if set(objects) != set(CONTAINERS):
        raise EvidenceHold("incomplete production container identity inventory")
    records: dict[str, dict[str, Any]] = {}
    reference_env: dict[str, dict[str, str]] = {}
    for name in CONTAINERS:
        obj = objects[name]
        if not isinstance(obj, dict) or obj.get("Name") != "/" + name:
            raise EvidenceHold("container runtime identity mismatch")
        identifier = obj.get("Id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise EvidenceHold("invalid immutable container identity")
        claim = _image_claim(obj)
        state = obj.get("State") or {}
        if state.get("Status") not in ("running", "exited", "created", "restarting"):
            raise EvidenceHold("unknown container runtime status")
        networks = (obj.get("NetworkSettings") or {}).get("Networks") or {}
        if not isinstance(networks, dict):
            raise EvidenceHold("invalid network identity data")
        record = {
            "name": name,
            "container_id": obj.get("Id"),
            "status": state["Status"],
            "image": claim,
            "started_at": state.get("StartedAt"),
            "networks": sorted(networks),
        }
        records[name] = record
        if name in GRAPH_WORKLOADS:
            reference_env[name] = _env((obj.get("Config") or {}).get("Env"))
    user = _groups(reference_env, "NEO4J_USER")
    endpoint = _groups(reference_env, "NEO4J_URI")
    for name in GRAPH_WORKLOADS:
        records[name]["neo4j_principal_alias"] = user[name]
        records[name]["neo4j_endpoint_alias"] = endpoint[name]

    principals = list(user.values())
    all_identified = all(v != "UNKNOWN" for v in principals)
    shared_user = all_identified and len(set(principals)) == 1
    endpoints = list(endpoint.values())
    shared_endpoint = all(v != "UNKNOWN" for v in endpoints) and len(set(endpoints)) == 1
    findings = []
    if shared_user:
        findings.append("SHARED_NEO4J_PRINCIPAL_REFERENCE")
    if shared_endpoint:
        findings.append("SHARED_NEO4J_ENDPOINT_REFERENCE")
    if not all_identified:
        findings.append("MISSING_GRAPH_PRINCIPAL_IDENTITIES")
    findings.append("NO_VERIFIED_SOURCE_TO_IMAGE_ATTESTATION")
    return {
        "captured_at_utc": now,
        "evidence_tier": "PRODUCTION_READ_ONLY_DOCKER_INSPECT",
        "production_decision": "HOLD",
        "promotion_authorized": False,
        "no_production_mutations_performed": True,
        "findings": findings,
        "containers": [records[n] for n in CONTAINERS],
        "limitations": (
            "Docker configuration evidence is not effective SHOW PRIVILEGES; "
            "labels are not signed build attestations; runtime images may have "
            "changed since this snapshot."
        ),
    }


def collect() -> dict[str, Any]:
    objects = {n: _docker_inspect(n) for n in CONTAINERS}
    return build_report(
        objects, datetime.now(UTC).isoformat(timespec="seconds")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit redacted JSON")
    args = parser.parse_args(argv)
    if not args.json:
        parser.error("explicit --json required")
    try:
        result = collect()
    except EvidenceHold as exc:
        print(json.dumps({"production_decision": "HOLD",
                          "error": str(exc), "promotion_authorized": False}))
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
