#!/usr/bin/env python3
"""Explicit offline full FastAPI auth acceptance in an immutable Docker image.

Only synthetic data, Basic credentials and header names are passed. Never
connects to, execs into, or changes the running AssistX/Neo4j/Redis services.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def docker_command(image_id: str, root: Path, mode: str) -> list[str]:
    if not isinstance(image_id, str) or not SHA.fullmatch(image_id):
        raise ValueError("an immutable image ID is required")
    if mode not in ("synthetic-explicit-opt-in", "synthetic-trusted-header-research"):
        raise ValueError("unexpected synthetic acceptance mode")
    root = root.resolve(strict=True)
    for relative in ("src", "templates", "static", "scripts/trace_read_api_auth_canary.py"):
        if not (root / relative).exists():
            raise ValueError("missing checked source component")
    mounts = []
    for relative in ("src", "templates", "static", "scripts/trace_read_api_auth_canary.py"):
        mounts += ["--mount", f"type=bind,src={root / relative},dst=/work/{relative},readonly"]
    header = "X-Synthetic-Proxy-Identity" if mode == "synthetic-trusted-header-research" else ""
    return [
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--ipc", "none", "--cpus", "0.5", "--memory", "768m",
        "--pids-limit", "100", "--read-only", "--tmpfs",
        "/tmp:rw,nosuid,size=64m", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", *mounts,
        "--workdir", "/tmp", "--entrypoint", "/usr/bin/env", image_id,
        "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/tmp",
        "PYTHONPATH=/work/src", "BASIC_AUTH_USER=synthetic-operator",
        "BASIC_AUTH_PASS=synthetic-test-password-only",
        f"TRUSTED_AUTH_HEADER={header}",
        f"ASSISTX_ISOLATED_TRACE_AUTH_CANARY={mode}",
        "ASSISTX_DEPENDENCY_MODE=production", "ASSISTX_RUNTIME_PROFILE=production",
        "TRANSCRIPTIONS_ROOT=/tmp/transcriptions", "CAPTURES_ROOT=/tmp/captures",
        "/usr/local/bin/python", "/work/scripts/trace_read_api_auth_canary.py",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-isolated-docker-canary", action="store_true")
    parser.add_argument("--expected-image-id")
    parser.add_argument("--expected-source-sha")
    args = parser.parse_args(argv)
    if not args.approve_isolated_docker_canary or not args.expected_image_id or not args.expected_source_sha:
        print("BLOCKED: explicit approval and both image/source SHAs required", file=sys.stderr)
        return 2
    if not SHA.fullmatch(args.expected_image_id) or not COMMIT.fullmatch(args.expected_source_sha):
        print("BLOCKED: invalid pinned identity", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True, timeout=8).stdout.strip()
        if head != args.expected_source_sha:
            raise RuntimeError("source revision does not match approved commit")
        image = subprocess.run(["docker", "image", "inspect", args.expected_image_id,
                                "--format", "{{.Id}}"], check=True,
                               capture_output=True, text=True, timeout=12).stdout.strip()
        if image != args.expected_image_id:
            raise RuntimeError("cached image is not the approved SHA")
        for mode in ("synthetic-explicit-opt-in", "synthetic-trusted-header-research"):
            command = docker_command(image, root, mode)
            result = subprocess.run(command, capture_output=True, text=True, timeout=75)
            if result.returncode != 0:
                print(f"FAILED: isolated {mode} exit={result.returncode}", file=sys.stderr)
                print(result.stdout[-500:], file=sys.stderr)
                return 1
            if mode == "synthetic-explicit-opt-in":
                assert "ISOLATED_FASTAPI_TRACE_AUTH_CANARY_PASS" in result.stdout
            else:
                assert "CONDITIONAL_TRUST_BOUNDARY_BLOCKED_UNTIL_UPSTREAM_STRIPPING_PROVEN" in result.stdout
            print(f"PASS {mode}: isolated full-app acceptance")
        print("FULL_APP_ISOLATED_STAGING_CANARY_PASS")
        return 0
    except (OSError, subprocess.SubprocessError, RuntimeError, AssertionError, ValueError) as exc:
        print("BLOCKED/FAILED: " + str(exc)[:180], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
