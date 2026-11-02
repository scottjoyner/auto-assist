#!/usr/bin/env python3
"""Read-only local K2 model compatibility evidence; NEVER loads or admits a model.

An estimate-only LM Studio call is optional. A PASS on constituent checks is
NOT an operator approval or proof of successful decoding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

MODEL = Path("/home/scott/.lmstudio/models/IFM/K2-Horizon-0.9B-GGUF/K2-Horizon-1B-BF16.gguf")
EXPECTED_SHA256 = "371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8"
EXPECTED_REVIEWED_APP = "0.4.26+4"
APP_PACKAGE = Path("/opt/LM-Studio/resources/app/package.json")
CLI = Path("/home/scott/.lmstudio/bin/lms")
APP_CLI = Path("/opt/LM-Studio/resources/app/.webpack/lms")
URL = "http://127.0.0.1:1234/v1/models"
MODEL_ID = "k2-horizon-0.9b"
CONTEXT_LENGTH = 64000
PARALLEL = 4
MIN_AVAILABLE_BYTES = 8 * 1024**3
MIN_ROOT_FREE_BYTES = 10 * 1024**3
LIMIT = 128 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def available_memory_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable missing")


def root_free_bytes() -> int:
    info = os.statvfs("/")
    return info.f_bavail * info.f_frsize


def api_catalog() -> dict:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    with opener.open(urllib.request.Request(URL, method="GET"), timeout=4) as response:
        if response.status != 200:
            raise ValueError("API returned non-200 status")
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("API response exceeded limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("error") is not None:
        raise ValueError("API error response")
    items = value.get("data")
    if not isinstance(items, list):
        raise ValueError("API catalog is not a list")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in items):
        raise ValueError("invalid catalog model identity")
    return {"available": True,
            "loaded_models": len(items),
            "expected_model_loaded": any(item["id"] == MODEL_ID for item in items)}


def estimate_only() -> dict:
    if not CLI.is_file():
        return {"status": "unavailable_cli", "estimated_total_gib": None}
    # Pin HOME to physical owner; connector sandboxes otherwise use a separate
    # LM Studio identity and return Invalid passkey. Never print CLI output:
    # future versions could return sensitive metadata.
    env = os.environ.copy()
    env["HOME"] = "/home/scott"
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("ALL_PROXY", None)
    try:
        result = subprocess.run([
            str(CLI), "load", MODEL_ID,
            "--identifier", MODEL_ID,
            "--context-length", str(CONTEXT_LENGTH),
            "--parallel", str(PARALLEL),
            "--estimate-only",
        ], capture_output=True, text=True, timeout=25, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": type(exc).__name__, "estimated_total_gib": None}
    if result.returncode != 0:
        return {"status": "rejected_by_cli", "estimated_total_gib": None}
    # Current LM Studio 0.4.28+2 prints estimate diagnostics to stderr,
    # despite a successful exit code. Treat both streams as untrusted and
    # parse only the exact bounded total-memory signal, never persist text.
    estimate_text = (result.stdout[:8192] + "\n" + result.stderr[:8192])
    match = re.search(r"Estimated Total Memory:\s*([0-9]+(?:\.[0-9]+)?)\s*GiB", estimate_text)
    return {"status": "estimate_succeeded" if match else "estimate_unparsed",
            "estimated_total_gib": float(match.group(1)) if match else None}


def observe(*, with_estimate: bool) -> dict:
    findings: dict = {
        "kind": "x1-k2-version-qualification-readonly-v1",
        "mode": "non_admitting",
        "model_calls": 0, "model_loads": 0, "neo4j_writes": 0,
        "approval_issued": False, "can_dispatch": False,
        "required_model_id": MODEL_ID,
        "expected_version": EXPECTED_REVIEWED_APP,
        "context_tokens": CONTEXT_LENGTH, "parallel_slots": PARALLEL,
    }
    try:
        version = json.loads(APP_PACKAGE.read_text()).get("version")
        findings["installed_version"] = str(version) if version else None
    except (OSError, ValueError, TypeError):
        findings["installed_version"] = None
    findings["reviewed_version_match"] = (
        findings["installed_version"] == EXPECTED_REVIEWED_APP
    )
    try:
        findings["model_sha256_match"] = sha256_file(MODEL) == EXPECTED_SHA256
    except (OSError, ValueError):
        findings["model_sha256_match"] = False
    try:
        findings["cli_matches_installed_app"] = (
            CLI.is_file() and APP_CLI.is_file()
            and sha256_file(CLI) == sha256_file(APP_CLI)
        )
    except (OSError, ValueError):
        findings["cli_matches_installed_app"] = False
    try:
        findings["memory_guard_pass"] = available_memory_bytes() >= MIN_AVAILABLE_BYTES
        findings["root_space_guard_pass"] = root_free_bytes() >= MIN_ROOT_FREE_BYTES
    except (OSError, ValueError):
        findings["memory_guard_pass"] = False
        findings["root_space_guard_pass"] = False
    try:
        findings["api"] = api_catalog()
    except Exception as exc:
        findings["api"] = {"available": False, "error_type": type(exc).__name__}
    findings["estimate"] = estimate_only() if with_estimate else {"status": "not_requested"}
    # Even a successful estimate is not a decode canary and cannot waive the
    # exact version pin in the separately deployed resident restoration.
    findings["gate"] = "HOLD_REQUIRES_OPERATOR_REVIEW"
    findings["reasons"] = []
    for field in ("reviewed_version_match", "model_sha256_match",
                  "cli_matches_installed_app", "memory_guard_pass",
                  "root_space_guard_pass"):
        if not findings[field]:
            findings["reasons"].append(field + "_failed")
    if not findings["api"]["available"]:
        findings["reasons"].append("local_model_api_unavailable")
    if with_estimate and findings["estimate"]["status"] != "estimate_succeeded":
        findings["reasons"].append("model_memory_estimate_unavailable")
    findings["reasons"].append("isolated_decode_canary_and_signed_approval_not_proven")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimate-only", action="store_true",
                        help="Ask installed LM Studio to estimate model memory; never load")
    args = parser.parse_args()
    result = observe(with_estimate=args.estimate_only)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
