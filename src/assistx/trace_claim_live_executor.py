"""Fail-closed actual AssistX claim -> two-proof -> fsynced no-op adapter.

This module neither creates nor claims tasks. It consumes an existing AssistX
claim and NEVER runs shell, LLM, arbitrary commands, or remote scripts.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .trace_claim_lease import is_trace_probe_task, load_verifier, verify_lease_proof
from .trace_execution_adapter import TraceDenied, TraceReceiptStore, run_trace_probe

TRUTH = frozenset({"1", "true", "yes", "on"})
NODE_ALLOWLIST = frozenset({"xwing", "scotts-macbook-air"})


def _enabled(env: dict[str, str]) -> bool:
    return all(
        env.get(k, "false").lower() in TRUTH
        for k in (
            "FLEET_TRACE_PROBE_ENABLED",
            "FLEET_TRACE_REAL_EXECUTION_ENABLED",
        )
    )


def _safe_url(value: str) -> str:
    """Accept only a complete HTTPS origin (localhost HTTP for tests)."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise TraceDenied("unsafe_lease_issuer_url")
    try:
        split = urlsplit(value)
        _ = split.port
    except ValueError as exc:
        raise TraceDenied("unsafe_lease_issuer_url") from exc
    if (
        not split.hostname
        or any(ord(char) < 32 for char in value)
        or split.username is not None
        or split.password is not None
        or split.query
        or split.fragment
        or "?" in value
        or "#" in value
        or split.path not in ("", "/")
    ):
        raise TraceDenied("unsafe_lease_issuer_url")
    if split.scheme == "http":
        if split.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise TraceDenied("lease_issuer_plaintext_remote_denied")
    elif split.scheme != "https":
        raise TraceDenied("lease_issuer_https_required")
    return value.rstrip("/")


def require_pinned_issuer(assistx_url: str, env: dict[str, str]) -> str:
    """Reject an unapproved issuer origin before node credentials are sent."""
    expected = env.get("FLEET_TRACE_ISSUER_ORIGIN", "")
    if not expected:
        raise TraceDenied("missing_pinned_issuer_origin")
    url = _safe_url(assistx_url)
    if url != _safe_url(expected):
        raise TraceDenied("issuer_origin_not_pinned")
    return url


def preflight(
    *,
    node_id: str,
    audit_root: str,
    env: dict[str, str],
) -> None:
    """No side effects; used both for worker admission and before execution."""
    if node_id not in NODE_ALLOWLIST:
        raise TraceDenied("unapproved_real_execution_node")
    if not _enabled(env):
        raise TraceDenied("real_trace_execution_disabled")
    if not env.get("FLEET_NODE_AUTH_TOKEN"):
        raise TraceDenied("missing_fleet_node_identity_token")
    if not env.get("FLEET_TRACE_ISSUER_ORIGIN"):
        raise TraceDenied("missing_pinned_issuer_origin")
    _safe_url(env["FLEET_TRACE_ISSUER_ORIGIN"])
    key_path = env.get("FLEET_TRACE_LEASE_VERIFIER_KEY_FILE", "")
    if not key_path:
        raise TraceDenied("missing_real_issuer_verification_key")
    load_verifier(Path(key_path))
    store = TraceReceiptStore(audit_root, node_id=node_id)
    # The current encrypted NAS snapshot writer supports at most 8 MiB.
    # Halt *before* reaching that ceiling; never truncate an existing journal.
    if os.path.lexists(store.path):
        if store.path.lstat().st_size >= 7 * 1024 * 1024:
            raise TraceDenied("trace_journal_archive_capacity_near_limit")
        store.verify()
    try:
        volume = os.statvfs(store.root)
    except OSError as exc:
        raise TraceDenied("trace_disk_capacity_unavailable") from exc
    if volume.f_bavail * volume.f_frsize < 128 * 1024 * 1024:
        raise TraceDenied("trace_disk_capacity_too_low")


def execute_authorized_probe(
    *,
    task: dict[str, Any],
    node_id: str,
    claim_id: str,
    audit_root: str,
    assistx_url: str,
    auth: tuple[str, str] | None,
    env: dict[str, str],
    http: Callable[..., tuple[int, Any]],
) -> dict[str, Any]:
    """Two separate live API calls, node-chosen nonce, then durable no-op.

    Fails with TraceDenied and writes no journal when any status or signature
    check fails. A valid signed status is still bounded freshness, NOT a
    durable revocation projection or a general executor permission.
    """
    preflight(node_id=node_id, audit_root=audit_root, env=env)
    if not is_trace_probe_task(task) or task.get("target_agent_id") != node_id:
        raise TraceDenied("real_claim_task_scope_invalid")
    if task.get("claimed_by") != node_id or task.get("claim_id") != claim_id:
        raise TraceDenied("real_claim_lineage_invalid")
    attempt = task.get("execution_attempt")
    if type(attempt) is not int or attempt < 1:
        raise TraceDenied("missing_real_claim_generation")
    if task.get("status") not in {"CLAIMED", "RUNNING"}:
        raise TraceDenied("real_claim_inactive")
    payload = task.get("payload")
    if payload is None:
        import json

        try:
            payload = json.loads(task.get("payload_json", ""))
        except (TypeError, ValueError) as exc:
            raise TraceDenied("invalid_real_probe_payload") from exc
    if not isinstance(payload, dict) or payload != {"command_id": "probe.noop.v1"}:
        raise TraceDenied("real_probe_only_noop")
    # Request only a typed no-op; no task ID is allowed to redirect requests
    # to a different host, path, or service.
    url = _safe_url(assistx_url)
    if url != _safe_url(env["FLEET_TRACE_ISSUER_ORIGIN"]):
        raise TraceDenied("issuer_origin_not_pinned")
    headers = {"X-Fleet-Node-Token": env["FLEET_NODE_AUTH_TOKEN"]}
    proof_path = "/api/fleet/trace-execution/claim-lease-proof"
    status_code, response = http(
        "POST",
        url + proof_path,
        auth=auth,
        headers=headers,
        data={
            "node_id": node_id,
            "task_id": task["id"],
            "claim_id": claim_id,
            "execution_attempt": attempt,
        },
        timeout=5,
    )
    if status_code != 200 or not isinstance(response, dict) or not isinstance(response.get("lease_proof"), dict):
        raise TraceDenied("real_claim_lease_issuer_unavailable")
    proof = response["lease_proof"]
    challenge = secrets.token_hex(32)
    status_code, response = http(
        "POST",
        url + "/api/fleet/trace-execution/claim-current-status",
        auth=auth,
        headers=headers,
        data={"lease_proof": proof, "challenge": challenge},
        timeout=5,
    )
    if status_code != 200 or not isinstance(response, dict) or not isinstance(response.get("current_status"), dict):
        raise TraceDenied("real_claim_current_state_unavailable")
    signed_hash = verify_lease_proof(
        proof,
        node_id=node_id,
        verifier=load_verifier(Path(env["FLEET_TRACE_LEASE_VERIFIER_KEY_FILE"])),
        current_status=response["current_status"],
        challenge=challenge,
    )
    if (
        proof["task_id"] != task["id"]
        or proof["claim_id"] != claim_id
        or proof["execution_attempt"] != attempt
        or proof["command_id"] != "probe.noop.v1"
    ):
        raise TraceDenied("real_lease_does_not_match_claim")
    # No network request or unbounded computation between fresh verification
    # and journal preparation. This is the first, bounded-live *no-op* gate.
    result = run_trace_probe(
        {**task, "payload": payload},
        node_id=node_id,
        claim_id=claim_id,
        audit_root=audit_root,
        enabled=True,
        signed_grant_sha256=signed_hash,
    )
    return {**result, "assistx_claim_verified": True, "proof_kind": "assistx.claim-lease-proof.v1"}
