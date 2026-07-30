#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any


FORBIDDEN_SOURCE_PREFIXES = (
    "/home/scott/.ssh",
    "/home/scott/git",
    "/media/scott/SSD_4TB",
    "/media/scott/NAS5",
    "/var/run/docker.sock",
    "/run/docker.sock",
)
FORBIDDEN_TARGETS = {
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/root/.ssh",
    "/home/scott/.ssh",
}
ALLOWED_BIND_TARGETS = {"/app/hermes-home", "/app/artifacts", "/workspace"}
REQUIRED_ENV = {
    "AUTO_ASSIGN_BASE_URL": "",
    "HERMES_SELFTASK_ENABLED": "false",
    "FLEET_UNSAFE_SHELL_TASKS_ENABLED": "false",
    "ASSISTX_TOOL_EGRESS_MODE": "disabled",
}
FORBIDDEN_CAPABILITY_TOKENS = {"web", "search", "browser", "mcp", "cronjob", "delegation"}
PUBLIC_KEY_NAMES = {
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLOUDFLARE_API_TOKEN",
}


def _environment(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    result: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        key, separator, value = str(item).partition("=")
        result[key] = value if separator else ""
    return result


def _volume_parts(volume: Any) -> tuple[str | None, str | None, bool]:
    if isinstance(volume, str):
        pieces = volume.split(":")
        source = pieces[0] if len(pieces) >= 2 else None
        target = pieces[1] if len(pieces) >= 2 else pieces[0]
        read_only = any(piece == "ro" for piece in pieces[2:])
        return source, target, read_only
    if isinstance(volume, dict):
        source = volume.get("source")
        target = volume.get("target")
        read_only = bool(volume.get("read_only"))
        return str(source) if source else None, str(target) if target else None, read_only
    return None, None, False


def validate(payload: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    service = (payload.get("services") or {}).get("hermes-adapter")
    if not isinstance(service, dict):
        return ["hermes-adapter service is missing from rendered Compose"]

    if "executor" not in (service.get("profiles") or []):
        failures.append("hermes-adapter must remain behind the executor profile")
    if bool(service.get("privileged")):
        failures.append("privileged mode is forbidden")
    if not bool(service.get("read_only")):
        failures.append("container root filesystem must be read-only")
    if str(service.get("network_mode") or "").lower() == "host":
        failures.append("host network mode is forbidden")
    if str(service.get("pid") or "").lower() == "host":
        failures.append("host PID namespace is forbidden")
    if str(service.get("ipc") or "").lower() == "host":
        failures.append("host IPC namespace is forbidden")
    if service.get("devices"):
        failures.append("direct device mappings are forbidden for the default executor")
    if service.get("extra_hosts"):
        failures.append("extra_hosts must be empty; use the approved reconciliation network")

    user = str(service.get("user") or "").strip()
    if not user or user.split(":", 1)[0] in {"0", "root"}:
        failures.append("executor must run as a non-root user")

    cap_drop = {str(item).upper() for item in service.get("cap_drop") or []}
    if "ALL" not in cap_drop:
        failures.append("cap_drop must include ALL")
    security_opt = {str(item).lower() for item in service.get("security_opt") or []}
    if not any(item.startswith("no-new-privileges") for item in security_opt):
        failures.append("no-new-privileges security option is required")

    environment = _environment(service)
    for key, expected in REQUIRED_ENV.items():
        actual = environment.get(key)
        if actual is None or actual.strip().lower() != expected:
            failures.append(f"{key} must equal {expected!r}")
    for key in PUBLIC_KEY_NAMES:
        if environment.get(key, "").strip():
            failures.append(f"public provider credential {key} must be empty")
    for key in ("HERMES_AGENT_CAPABILITIES", "HERMES_TOOLSETS"):
        tokens = {token.strip().lower() for token in environment.get(key, "").split(",") if token.strip()}
        forbidden = sorted(tokens & FORBIDDEN_CAPABILITY_TOKENS)
        if forbidden:
            failures.append(f"{key} contains forbidden capability tokens: {', '.join(forbidden)}")

    expected_home = pathlib.Path(
        os.getenv("RECONCILIATION_HERMES_HOME", "artifacts/reconciliation-hermes-home")
    ).resolve()
    expected_workspace_raw = os.getenv("ASSISTX_EXECUTOR_WORKTREE", "").strip()
    expected_workspace = pathlib.Path(expected_workspace_raw).resolve() if expected_workspace_raw else None

    seen_targets: set[str] = set()
    for volume in service.get("volumes") or []:
        source, target, _ = _volume_parts(volume)
        if target:
            seen_targets.add(target)
        if target in FORBIDDEN_TARGETS:
            failures.append(f"forbidden mount target: {target}")
        if not source or not source.startswith("/"):
            continue
        resolved = pathlib.Path(source).resolve()
        allowed = False
        if target == "/app/hermes-home" and resolved == expected_home:
            allowed = True
        elif target == "/app/artifacts" and resolved.name == "artifacts":
            allowed = True
        elif target == "/workspace" and expected_workspace is not None and resolved == expected_workspace:
            allowed = True
        if target not in ALLOWED_BIND_TARGETS or not allowed:
            failures.append(f"unapproved bind mount: {resolved} -> {target}")
        for prefix in FORBIDDEN_SOURCE_PREFIXES:
            forbidden = pathlib.Path(prefix)
            try:
                resolved.relative_to(forbidden)
            except ValueError:
                continue
            if not (target == "/workspace" and expected_workspace is not None and resolved == expected_workspace):
                failures.append(f"broad or sensitive host source is forbidden: {resolved}")

    if "/app/hermes-home" not in seen_targets:
        failures.append("scoped Hermes home mount is required")
    if "/app/artifacts" not in seen_targets:
        failures.append("scoped evidence mount is required")

    return sorted(set(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the rendered Hermes executor containment contract.")
    parser.add_argument("rendered_compose", type=pathlib.Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.rendered_compose.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot read rendered Compose JSON: {exc}", file=sys.stderr)
        return 2

    failures = validate(payload)
    if failures:
        print("EXECUTOR_CONTAINMENT: BLOCKED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("EXECUTOR_CONTAINMENT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
