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


def build_operations_readiness(env: Mapping[str, str]) -> dict[str, Any]:
    signing_keys = _mapping(env, "ASSISTX_RUNBOOK_SIGNING_KEYS")
    verify_keys = _mapping(env, "FLEET_RUNBOOK_VERIFY_KEYS")
    node_tokens = _mapping(env, "ASSISTX_FLEET_NODE_TOKENS")
    service_aliases = _mapping(env, "FLEET_RECOVERY_SERVICE_ALIASES")
    compose_projects = _mapping(env, "FLEET_RECOVERY_COMPOSE_PROJECTS")
    active_key = env.get("ASSISTX_RUNBOOK_ACTIVE_KEY_ID", "").strip()
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
            "ready": bool(active_key and signing_keys.get(active_key)),
            "detail": f"Active key ID: {active_key or 'not configured'}",
        },
        {
            "id": "verification_key",
            "label": "Runbook verification key",
            "ready": bool(active_key and verify_keys.get(active_key)),
            "detail": "The active signing key ID must exist on nodes.",
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
