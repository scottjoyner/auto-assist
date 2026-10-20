#!/usr/bin/env python3
"""Check real Compose JSON, or render synthetic inputs without a Docker daemon.

Only invariant names are printed; rendered environments may contain secrets.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def validate(model):
    errors = []
    services = model.get("services", {})
    for name in ("api", "worker", "hermes-adapter", "redis", "neo4j"):
        service = services.get(name)
        if not service:
            errors.append(f"{name}: missing service")
            continue
        if service.get("container_name") != f"assistx-canary-{name}":
            errors.append(f"{name}: container identity is not isolated")
        if set(service.get("networks", {})) != {"default"}:
            errors.append(f"{name}: network is not isolated")
        if service.get("privileged") or service.get("network_mode") or service.get("pid"):
            errors.append(f"{name}: host authority is forbidden")
        for mount in service.get("volumes", []):
            volume = model.get("volumes", {}).get(mount.get("source"), {})
            if mount.get("type") != "volume" or volume.get("external") or volume.get("driver_opts"):
                errors.append(f"{name}: host or external mount is forbidden")
            elif not volume.get("name", "").startswith(model.get("name", "") + "_"):
                errors.append(f"{name}: volume is not project-scoped")
        for port in service.get("ports", []):
            if port.get("host_ip") != "127.0.0.1":
                errors.append(f"{name}: published port is not loopback-only")
        if name in {"worker", "hermes-adapter", "redis"} and service.get("ports"):
            errors.append(f"{name}: unexpected published port")
        if name in {"api", "worker", "hermes-adapter"}:
            env = service.get("environment", {})
            for key in ("FLEET_UNSAFE_SHELL_TASKS_ENABLED", "HERMES_SELFTASK_ENABLED"):
                if env.get(key) != "false":
                    errors.append(f"{name}: unsafe execution flag {key}")
            if env.get("REDIS_URL") != "redis://redis:6379/0":
                errors.append(f"{name}: Redis must use isolated service DNS")
            if name != "api" and env.get("ASSISTX_URL") != "http://api:8000":
                errors.append(f"{name}: API must use isolated service DNS")
    if "canary-executor" not in services.get("hermes-adapter", {}).get("profiles", []):
        errors.append("hermes-adapter: explicit opt-in profile is missing")
    network = model.get("networks", {}).get("default", {})
    if network.get("external") or network.get("name") != "assistx_canary_default":
        errors.append("default: canary network identity mismatch")
    api_ports = services.get("api", {}).get("ports", [])
    if len(api_ports) != 1 or str(api_ports[0].get("target")) != "8000":
        errors.append("api: expected one API port")
    return errors


def render(root, command):
    # Empty environment prevents production credentials/config from influencing CI.
    with tempfile.TemporaryDirectory(prefix="assistx-compose-") as temporary:
        env_path = Path(temporary) / "canary.env"
        env_path.write_text((root / "deploy/canary.env.example").read_text())
        env_path.chmod(0o600)
        env = {"PATH": os.environ.get("PATH", ""), "ASSISTX_ENV_FILE": str(env_path)}
        argv = shlex.split(command) + ["--project-directory", str(root), "--env-file", str(env_path), "--profile", "*"]
        for name in ("docker-compose.yml", "compose.prod.yml", "compose.canary.yml"):
            argv += ["-f", str(root / name)]
        result = subprocess.run(argv + ["config", "--format", "json"], env=env, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError("Compose rendering failed; inspect locally (output suppressed to protect secrets)")
        return json.loads(result.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdin", action="store_true", help="Validate actual rendered deployment JSON")
    parser.add_argument("--compose-command", default="docker compose")
    args = parser.parse_args()
    try:
        model = json.load(sys.stdin) if args.stdin else render(Path(__file__).resolve().parents[1], args.compose_command)
        failures = validate(model)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print("FAIL: Compose input unavailable or invalid; no configuration values printed")
        raise SystemExit(1)
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        raise SystemExit(1)
    print("PASS: merged canary Compose isolation (no containers started)")
