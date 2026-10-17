"""Strict read-only two-pass local Docker image custody, NOT signed provenance.

No secrets, env values, repository names, mount paths or arbitrary labels are
printed. Never executes inside or changes a production container.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

CONTAINERS = (
    "assistx-api", "assistx-worker", "assistx-hermes-adapter",
    "neo4j", "git-router-1",
)
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX_ID = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
DAEMON_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
REF_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}@sha256:([0-9a-f]{64})\Z")
STARTED_AT = re.compile(r"\d{4}-\d\d-\d\dT[0-9:.]+(?:Z|[+-]\d\d:\d\d)\Z")
ACCEPTED_STATUS = frozenset(("running", "exited", "created", "restarting", "paused"))


class CustodyHold(RuntimeError):
    """Missing, inconsistent or untrusted Docker identity evidence."""


def _run(argv: list[str]) -> str:
    try:
        proc = subprocess.run(
            argv, text=True, capture_output=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CustodyHold("local Docker read-only command unavailable") from exc
    # NEVER echo Docker stderr/stdout on failure: may include credentials.
    if proc.returncode != 0 or len(proc.stdout) > 8_000_000:
        raise CustodyHold("local Docker identity inspection unavailable")
    return proc.stdout


def _daemon() -> str:
    if any(os.environ.get(k) for k in (
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH", "DOCKER_CONFIG",
    )):
        raise CustodyHold("remote Docker context or configuration override denied")
    if _run(["docker", "context", "show"]).strip() != "default":
        raise CustodyHold("nonlocal Docker context denied")
    endpoint = _run([
        "docker", "context", "inspect", "default", "--format",
        "{{.Endpoints.docker.Host}}",
    ]).strip()
    if endpoint != "unix:///var/run/docker.sock":
        raise CustodyHold("Docker daemon socket is not the pinned local endpoint")
    identity = _run(["docker", "info", "--format", "{{.ID}}"]).strip()
    if not DAEMON_ID.fullmatch(identity):
        raise CustodyHold("malformed local Docker daemon identity")
    return identity


def _inspect(kind: str, identity: str) -> dict[str, Any]:
    if kind not in ("container", "image"):
        raise CustodyHold("unsupported Docker inspect target")
    try:
        entries = json.loads(_run(["docker", kind, "inspect", identity]))
    except (TypeError, ValueError) as exc:
        raise CustodyHold("unparseable Docker inspect JSON") from exc
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise CustodyHold("ambiguous Docker inspect response")
    return entries[0]


def _image(image_id: str) -> dict[str, Any]:
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise CustodyHold("not an immutable local image ID")
    doc = _inspect("image", image_id)
    if doc.get("Id") != image_id:
        raise CustodyHold("container image ID disagrees with inspected image object")
    repo_refs = doc.get("RepoDigests") or []
    if not isinstance(repo_refs, list) or len(repo_refs) > 128:
        raise CustodyHold("malformed repository digest inventory")
    digests = set()
    for reference in repo_refs:
        if not isinstance(reference, str):
            raise CustodyHold("malformed repository digest type")
        match = REF_DIGEST.fullmatch(reference)
        if match is None:
            raise CustodyHold("malformed repository digest format")
        digests.add("sha256:" + match.group(1))
    labels = (doc.get("Config") or {}).get("Labels") or {}
    if not isinstance(labels, dict):
        raise CustodyHold("invalid image label metadata")
    rev = labels.get("org.opencontainers.image.revision")
    valid = isinstance(rev, str) and REVISION.fullmatch(rev) is not None
    return {
        "local_image_id": image_id,
        "manifest_digest_values": sorted(digests),
        "unsigned_revision_claim": rev if valid else None,
        "malformed_unsigned_revision": rev is not None and not valid,
    }


def _container(name: str) -> dict[str, Any]:
    if name not in CONTAINERS:
        raise CustodyHold("container outside fixed production allowlist")
    doc = _inspect("container", name)
    if doc.get("Name") != "/" + name:
        raise CustodyHold("requested container name/identity mismatch")
    cid = doc.get("Id")
    if not isinstance(cid, str) or not HEX_ID.fullmatch(cid):
        raise CustodyHold("invalid container ID")
    state = doc.get("State")
    if not isinstance(state, dict) or state.get("Status") not in ACCEPTED_STATUS:
        raise CustodyHold("unsupported container state")
    started = state.get("StartedAt")
    if not isinstance(started, str) or not STARTED_AT.fullmatch(started):
        raise CustodyHold("invalid container start timestamp")
    pid = state.get("Pid")
    if type(pid) is not int or pid < 0:
        raise CustodyHold("invalid observed container PID")
    networks = (doc.get("NetworkSettings") or {}).get("Networks")
    if not isinstance(networks, dict) or any(not isinstance(k, str) or not k for k in networks):
        raise CustodyHold("missing network attachment inventory")
    mounts = doc.get("Mounts")
    if not isinstance(mounts, list):
        raise CustodyHold("missing mount inventory")
    access: dict[str, int] = {}
    for entry in mounts:
        if not isinstance(entry, dict):
            raise CustodyHold("invalid mount record")
        kind = entry.get("Type")
        if kind not in ("bind", "volume", "tmpfs") or type(entry.get("RW")) is not bool:
            raise CustodyHold("unrecognized mount type or access flag")
        key = kind + ("_rw" if entry["RW"] else "_ro")
        access[key] = access.get(key, 0) + 1
    return {
        "name": name, "container_id": cid, "state": state["Status"],
        "started_at": started, "pid": pid,
        "network_names": sorted(networks),
        "mount_access_counts": access,
        "image": _image(doc.get("Image")),
    }


def observe() -> dict[str, Any]:
    daemon = _daemon()
    first = {n: _container(n) for n in CONTAINERS}
    if _daemon() != daemon:
        raise CustodyHold("Docker daemon changed during first pass")
    second = {n: _container(n) for n in CONTAINERS}
    if _daemon() != daemon:
        raise CustodyHold("Docker daemon changed during confirmation")
    if first != second:
        raise CustodyHold("Docker identity drift between read-only observations")
    findings = ["NO_INDEPENDENT_SIGNED_BUILD_ATTESTATION"]
    for item in first.values():
        image = item["image"]
        if not image["manifest_digest_values"]:
            findings.append("NO_MANIFEST_DIGEST:" + item["name"])
        if not image["unsigned_revision_claim"]:
            findings.append("NO_VALID_UNSIGNED_REVISION_LABEL:" + item["name"])
    return {
        "captured_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "evidence_tier": "PRODUCTION_LOCAL_DOCKER_READ_ONLY",
        "production_decision": "HOLD",
        "promotion_authorized": False,
        "signed_source_to_image_verified": False,
        "local_container_image_consistency_observed": True,
        "two_pass_identity_stable": True,
        "findings": sorted(findings),
        "containers": [first[n] for n in CONTAINERS],
        "limitations": (
            "Local Docker may be compromised; the manifest digest is not "
            "a signed build receipt. Image labels/tags are unsigned. Two "
            "inspections are non-atomic and do not prove independent custody."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if not args.json:
        p.error("explicit --json required")
    try:
        result = observe()
    except CustodyHold as exc:
        print(json.dumps({
            "production_decision": "HOLD", "promotion_authorized": False,
            "error": str(exc), "evidence_tier": "INCOMPLETE_READ_ONLY_EVIDENCE",
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
