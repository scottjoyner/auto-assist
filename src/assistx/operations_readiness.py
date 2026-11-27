from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _mapping(env: Mapping[str, str], name: str) -> dict[str, Any]:
    try:
        value = json.loads(env.get(name, "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _active_key_pair_ready(
    signing_keys: dict[str, Any],
    verify_keys: dict[str, Any],
    active_key: str,
) -> bool:
    """The active signing key and its node-side verify key must be the same
    non-empty secret. A mismatched pair cannot verify a signed runbook, so it
    must never make the control plane look ready to dispatch."""
    signing = signing_keys.get(active_key)
    verifying = verify_keys.get(active_key)
    if not active_key or not isinstance(signing, str) or not isinstance(verifying, str):
        return False
    if not signing.strip() or not verifying.strip():
        return False
    return signing == verifying


def build_operations_readiness(env: Mapping[str, str]) -> dict[str, Any]:
    signing_keys = _mapping(env, "ASSISTX_RUNBOOK_SIGNING_KEYS")
    verify_keys = _mapping(env, "FLEET_RUNBOOK_VERIFY_KEYS")
    node_tokens = _mapping(env, "ASSISTX_FLEET_NODE_TOKENS")
    service_aliases = _mapping(env, "FLEET_RECOVERY_SERVICE_ALIASES")
    compose_projects = _mapping(env, "FLEET_RECOVERY_COMPOSE_PROJECTS")
    repository_roots = _mapping(env, "ASSISTX_REPOSITORY_ROOTS_JSON")
    improvement_keys = _mapping(env, "ASSISTX_IMPROVEMENT_VERIFY_KEYS")
    active_key = env.get("ASSISTX_RUNBOOK_ACTIVE_KEY_ID", "").strip()
    keys_ready = _active_key_pair_ready(signing_keys, verify_keys, active_key)
    checks = [
        {
            "id": "control_execution",
            "label": "Control-plane recovery execution",
            "ready": env.get("ASSISTX_RECOVERY_EXECUTION_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            "detail": "Enable only after canary validation.",
        },
        {
            "id": "node_execution",
            "label": "Node typed-runbook execution",
            "ready": env.get("FLEET_RECOVERY_RUNBOOKS_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            "detail": "Each recovery node must opt in independently.",
        },
        {
            "id": "signing_key",
            "label": "Active signing key",
            "ready": keys_ready,
            "detail": f"Active key ID: {active_key or 'not configured'}",
        },
        {
            "id": "verification_key",
            "label": "Runbook verification key",
            "ready": keys_ready,
            "detail": "The active signing key ID must exist on nodes with matching material.",
        },
        {
            "id": "node_identity",
            "label": "Node identity registry",
            "ready": bool(node_tokens),
            "detail": f"{len(node_tokens)} node token(s) configured.",
        },
        {
            "id": "service_allowlist",
            "label": "Service adapter allowlist",
            "ready": bool(service_aliases),
            "detail": f"{len(service_aliases)} service alias(es) configured.",
        },
        {
            "id": "compose_allowlist",
            "label": "Compose project allowlist",
            "ready": bool(compose_projects),
            "optional": True,
            "detail": f"{len(compose_projects)} project(s) configured.",
        },
        {
            "id": "improvement_repositories",
            "label": "Improvement repository registry",
            "ready": bool(repository_roots),
            "optional": True,
            "detail": f"{len(repository_roots)} repository root(s) configured.",
        },
        {
            "id": "improvement_attestation",
            "label": "Improvement node verification keys",
            "ready": bool(improvement_keys),
            "optional": True,
            "detail": f"{len(improvement_keys)} node key(s) configured.",
        },
        {
            "id": "improvement_worktrees",
            "label": "Isolated improvement workspace",
            "ready": bool(
                env.get("ASSISTX_IMPROVEMENT_WORKTREE_ROOT", "").strip()
            ),
            "optional": True,
            "detail": "A dedicated worktree root is required on code-capable nodes.",
        },
        {
            "id": "kv_prefix_identity",
            "label": "Opaque KV prefix identity",
            "ready": bool(
                env.get("ASSISTX_KV_PREFIX_HMAC_SECRET", "").strip()
            ),
            "optional": True,
            "detail": (
                "Trusted prompt producers need keyed digest material; raw "
                "prompts and token arrays must not be cataloged."
            ),
        },
        {
            "id": "kv_cache_control",
            "label": "Node-local KV cache adapter",
            "ready": bool(env.get("FLEET_KV_CACHE_CONTROL_URL", "").strip()),
            "optional": True,
            "detail": (
                "Required only for runtime-specific export/restore. "
                "Affinity-only routing works without it."
            ),
        },
        {
            "id": "legacy_shell",
            "label": "Legacy shell disabled",
            "ready": env.get("FLEET_UNSAFE_SHELL_TASKS_ENABLED", "false").lower()
            not in {"1", "true", "yes"},
            "detail": "Generic command payloads should remain disabled.",
        },
    ]
    required = [check for check in checks if not check.get("optional")]
    return {
        "ready": all(check["ready"] for check in required),
        "checks": checks,
        "missing": [check["id"] for check in required if not check["ready"]],
        "safe_to_dispatch": all(check["ready"] for check in required),
    }
