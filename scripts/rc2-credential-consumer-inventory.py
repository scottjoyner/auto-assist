#!/usr/bin/env python3
"""Metadata-only inventory of credential consumers in three AssistX containers.

Never prints environment values, usernames, tokens, Docker inspect JSON,
network addresses, env file contents, hashes of secrets or credentials.
Read-only Docker inspect. Does NOT compare values to public Git history,
rotate any credential, or authorize production release.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

TARGETS = ("assistx-api", "assistx-worker", "assistx-hermes-adapter")
GROUPS = {
    "basic_auth": frozenset({"BASIC_AUTH_USER", "BASIC_AUTH_PASS"}),
    "trusted_identity": frozenset({"TRUSTED_AUTH_HEADER"}),
    "graph_access": frozenset({"NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_URI"}),
    "provider_or_api": frozenset({
        "API_TOKEN", "PAPERCLIP_API_KEY", "PAPERCLIP_API_TOKEN",
        "PAPERCLIP_TOKEN", "OPENAI_API_KEY", "WS_AUTH_TOKEN",
    }),
    "projection_signing": frozenset({
        "RUNTIME_PROJECTION_HMAC_SECRET",
        "RUNTIME_PROJECTION_SIGNING_KEY", "RUNTIME_PROJECTION_KEY_ID",
    }),
    "redis_auth": frozenset({"REDIS_PASSWORD", "REDIS_URL"}),
}
SENSITIVE_MARKERS = ("PASSWORD", "TOKEN", "SECRET", "API_KEY", "PRIVATE_KEY", "SIGNING_KEY")


def summarize(inspected: list[dict[str, Any]]) -> dict[str, object]:
    """Consume inspect JSON in memory; never retain or return any env value."""
    indexed: dict[str, dict[str, Any]] = {}
    for item in inspected:
        if not isinstance(item, dict):
            raise ValueError("malformed Docker inspection object")
        name = item.get("Name")
        if not isinstance(name, str) or not name.startswith("/"):
            raise ValueError("unverified Docker container name")
        name = name[1:]
        if name not in TARGETS or name in indexed:
            raise ValueError("unexpected or duplicate container")
        indexed[name] = item
    result = []
    for name in TARGETS:
        obj = indexed.get(name)
        if obj is None:
            result.append({"container": name, "state": "UNKNOWN", "groups": {},
                           "sensitive_variable_name_count": None})
            continue
        variables = obj.get("Config", {}).get("Env", [])
        if not isinstance(variables, list) or not all(
            isinstance(line, str) and "=" in line for line in variables
        ):
            raise ValueError("invalid Docker environment inspection")
        keys = {line.partition("=")[0] for line in variables}
        result.append({
            "container": name,
            "state": "RUNNING" if obj.get("State", {}).get("Running") is True else "STOPPED",
            "groups": {
                group: len(names.intersection(keys))
                for group, names in GROUPS.items()
            },
            "sensitive_variable_name_count": sum(
                any(marker in key.upper() for marker in SENSITIVE_MARKERS)
                for key in keys
            ),
        })
    return {
        "schema_version": "assistx.rc2.credential-consumer-names.v1",
        "evidence_scope": "docker_config_env_names_only",
        "containers": result,
        "possible_public_exposure": "UNRESOLVED",
        "owner_custody_required": True,
        "production_release_authorized": False,
        "credential_rotation_performed": False,
    }


def main() -> int:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--type", "container", *TARGETS],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if result.returncode:
            raise RuntimeError("Docker inventory failed")
        raw = json.loads(result.stdout)
        if not isinstance(raw, list):
            raise ValueError("bad Docker inventory response")
        report = summarize(raw)
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.TimeoutExpired):
        report = {
            "schema_version": "assistx.rc2.credential-consumer-names.v1",
            "evidence_scope": "unavailable",
            "inventory_status": "UNKNOWN",
            "owner_custody_required": True,
            "production_release_authorized": False,
            "credential_rotation_performed": False,
        }
        print(json.dumps(report, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    # A read-only inventory cannot establish that public credentials were rotated.
    return 2


if __name__ == "__main__":
    sys.exit(main())
