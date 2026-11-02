#!/usr/bin/env python3
"""Bounded, synthetic-only K2 decode canary. NEVER authorizes runtime admission.

Uses the existing shared LM Studio server; network isolation is NOT established.
A successful one-prompt canary is compatibility evidence, not fleet qualification.
Only performs the model load when --run is explicitly supplied.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PRE = ROOT / "scripts/check-x1-k2-compatibility.py"
LOCK = Path("/home/scott/.local/state/assistx-admission-preflight/restore-resident-k2.lock")
MODEL_ID = "k2-horizon-0.9b"
API = "http://127.0.0.1:1234/v1"
PROMPT = "Return exactly the word OK. This is a synthetic nonproduction canary."
MAX_COMPLETION = 192
MODEL_CONTEXT = 64000
PARALLEL = 4
MIN_FREE_ROOT = 15 * 1024**3
MIN_AVAILABLE_MEMORY = 16 * 1024**3
CLI = Path("/home/scott/.lmstudio/bin/lms")
EVIDENCE_DIR = Path("/home/scott/.local/state/assistx-admission-preflight")
ALLOWED_VERSION = "0.4.28+2"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_catalog() -> list[str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(urllib.request.Request(API + "/models", method="GET"), timeout=5) as res:
        if res.status != 200:
            raise RuntimeError("catalog status not 200")
        raw = res.read(131073)
    if len(raw) > 131072:
        raise RuntimeError("catalog too large")
    body = json.loads(raw)
    if not isinstance(body, dict) or body.get("error") is not None:
        raise RuntimeError("catalog is an error")
    rows = body.get("data")
    if not isinstance(rows, list) or not all(
            isinstance(r, dict) and isinstance(r.get("id"), str) for r in rows):
        raise RuntimeError("invalid model identities")
    return [r["id"] for r in rows]


def _cli(args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME="/home/scott")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    return subprocess.run([str(CLI), *args], env=env, text=True,
                          capture_output=True, timeout=timeout, check=False)


def _completion() -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    payload = json.dumps({
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0,
        "stream": False,
        "max_tokens": MAX_COMPLETION,
    }, separators=(",", ":")).encode()
    request = urllib.request.Request(API + "/chat/completions", data=payload,
             method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    t0 = time.monotonic()
    with opener.open(request, timeout=45) as res:
        if res.status != 200:
            raise RuntimeError("model completion did not return 200")
        response = res.read(262145)
    elapsed = round(time.monotonic() - t0, 3)
    if len(response) > 262144:
        raise RuntimeError("response exceeds safety limit")
    body = json.loads(response)
    if not isinstance(body, dict) or body.get("error") is not None:
        raise RuntimeError("completion error payload")
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("no completion choices")
    first = choices[0]
    message = first.get("message")
    if not isinstance(message, dict):
        message = {}
    content = message.get("content") if isinstance(message.get("content"), str) else first.get("text")
    visible = isinstance(content, str) and bool(content.strip())
    finish = first.get("finish_reason")
    finish = finish if finish in ("stop", "length", "tool_calls", "content_filter") else "other"
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    tt = usage.get("total_tokens")
    valid = all(type(x) is int and x >= 0 for x in (pt, ct, tt))
    if valid and (ct < 1 or ct > MAX_COMPLETION or tt < pt + ct):
        valid = False
    return {
        "completion_http": 200,
        "response_is_nonempty": bool(visible),
        "response_sha256": hashlib.sha256(content.encode()).hexdigest() if visible else None,
        "finish_reason": finish,
        "reasoning_content_field_present": bool(message.get("reasoning_content")),
        "usage_provider_reported": valid,
        "prompt_tokens": pt if valid else None,
        "completion_tokens": ct if valid else None,
        "total_tokens": tt if valid else None,
        "elapsed_seconds": elapsed,
        "output_tokens_per_second_end_to_end": round(ct / elapsed, 2) if valid and elapsed > 0 else None,
        "synthetic_prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
    }


def load_preflight():
    spec = importlib.util.spec_from_file_location("x1_k2_preflight", PRE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _admissible_for_canary(preflight: dict) -> list[str]:
    # This is a separate experimental compatibility gate; it never alters
    # the production-reviewed version or accepts another unexpected version.
    checks = []
    if preflight.get("installed_version") != ALLOWED_VERSION:
        checks.append("unreviewed_installed_app_version")
    for key in ("model_sha256_match", "cli_matches_installed_app",
                "memory_guard_pass", "root_space_guard_pass"):
        if preflight.get(key) is not True:
            checks.append(f"{key}_not_verified")
    api = preflight.get("api") or {}
    if api.get("available") is not True or api.get("loaded_models") != 0:
        checks.append("api_unavailable_or_models_already_loaded")
    estimate = preflight.get("estimate") or {}
    if estimate.get("status") != "estimate_succeeded":
        checks.append("estimate_only_not_verified")
    if (not isinstance(estimate.get("estimated_total_gib"), (float, int))
        or estimate.get("estimated_total_gib") > 8.0):
        checks.append("memory_estimate_exceeds_cap_or_missing")
    if preflight.get("root_space_guard_pass") and load_preflight().root_free_bytes() < MIN_FREE_ROOT:
        checks.append("root_free_below_canary_floor")
    if preflight.get("memory_guard_pass") and load_preflight().available_memory_bytes() < MIN_AVAILABLE_MEMORY:
        checks.append("memory_available_below_canary_floor")
    return checks


def run_canary() -> dict:
    report = {"kind": "k2-0428-synthetic-decode-canary-v1",
              "mode": "synthetic_nonproduction_shared_local_server",
              "network_isolation_proven": False,
              "model_id": MODEL_ID,
              "app_version_expected_for_test": ALLOWED_VERSION,
              "context_length": MODEL_CONTEXT, "parallel_slots": PARALLEL,
              "production_admission_renewed": False,
              "production_router_requests": 0,
              "paid_api_requests": 0,
              "live_traces_processed": 0,
              "authorizes_dispatch": False,
              "result": "HOLD",
              "cleanup": {"unload_attempted": False, "confirmed_absent": False}}
    module = load_preflight()
    # Never trust an old evidence snapshot; preflight and lock happen now.
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report["reason"] = "k2_restorer_lock_held"
            return report
        preflight = module.observe(with_estimate=True)
        report["preflight"] = {
            key: preflight.get(key) for key in (
                "installed_version", "model_sha256_match", "cli_matches_installed_app",
                "memory_guard_pass", "root_space_guard_pass")
        }
        report["estimate_total_gib"] = (preflight.get("estimate") or {}).get("estimated_total_gib")
        issues = _admissible_for_canary(preflight)
        if issues:
            report["reason"] = "preflight_blocked"
            report["blocking_checks"] = issues
            return report
        load_attempted = False
        try:
            if check_catalog():
                report["reason"] = "concurrent_model_presence"
                return report
            if Path("/home/scott/.config/assistx-fleet/hold-resident-k2").exists():
                report["reason"] = "operator_hold_active"
                return report
            load_attempted = True
            load_out = _cli(["load", MODEL_ID, "--identifier", MODEL_ID,
                    "--context-length", str(MODEL_CONTEXT), "--parallel", str(PARALLEL),
                    "--ttl", "90", "--yes"], timeout=100)
            report["load_exit_code"] = load_out.returncode
            # Never persist terminal output. It could change across versions.
            if load_out.returncode != 0:
                report["reason"] = "load_failed"
                return report
            current = check_catalog()
            if current != [MODEL_ID]:
                report["reason"] = "unexpected_loaded_inventory"
                return report
            report["model_loaded_for_test"] = True
            report["completion"] = _completion()
            report["result"] = (
                "CANARY_COMPLETED_MEASURED"
                if report["completion"]["usage_provider_reported"] and report["completion"]["response_is_nonempty"]
                else "HOLD_NO_VISIBLE_COMPLETION" if not report["completion"]["response_is_nonempty"]
                else "HOLD_USAGE_UNVERIFIED")
            report["reason"] = (
                "decode_completed_unapproved"
                if report["result"] == "CANARY_COMPLETED_MEASURED"
                else "decode_response_insufficient_for_qualification")
        except Exception as exc:
            report["reason"] = "canary_exception_" + type(exc).__name__
            # Only emit exact error codes authored in this script, never raw
            # model/server error strings or HTTP response bodies.
            fixed = {
                "catalog status not 200", "catalog too large",
                "catalog is an error", "invalid model identities",
                "model completion did not return 200", "response exceeds safety limit",
                "completion error payload", "no completion choices",
            }
            if type(exc) is RuntimeError and str(exc) in fixed:
                report["safe_error_code"] = str(exc).replace(" ", "_")
        finally:
            if load_attempted:
                report["cleanup"]["unload_attempted"] = True
                try:
                    result = _cli(["unload", MODEL_ID], timeout=35)
                    report["cleanup"]["unload_exit_code"] = result.returncode
                except Exception as exc:
                    report["cleanup"]["unload_error"] = type(exc).__name__
                try:
                    report["cleanup"]["confirmed_absent"] = MODEL_ID not in check_catalog()
                except Exception as exc:
                    report["cleanup"]["post_unload_error"] = type(exc).__name__
                if not report["cleanup"]["confirmed_absent"]:
                    report["result"] = "HOLD_CLEANUP_UNVERIFIED"
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="Explicitly allow one local synthetic load+decode+unload")
    parser.add_argument("--evidence-output", type=Path,
                        help="Immutable new JSON evidence file (required for --run)")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"result": "DRY_RUN_ONLY", "model_loads": 0,
                          "authorizes_dispatch": False}, sort_keys=True))
        return 0
    if args.evidence_output is None:
        parser.error("--run requires --evidence-output")
    output = args.evidence_output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        parser.error("output already exists")
    if output.parent != EVIDENCE_DIR:
        parser.error("output must be directly under approved evidence directory")
    result = run_canary()
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    # Final output can be written even on failure; exclusively new file.
    fd = os.open(str(output), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(encoded)
    print(json.dumps({"result": result["result"], "reason": result.get("reason"),
                      "cleanup": result["cleanup"], "evidence": str(output),
                      "evidence_sha256": hashlib.sha256(encoded.encode()).hexdigest()},
                     sort_keys=True))
    return 0 if result["result"] == "CANARY_COMPLETED_MEASURED" and result["cleanup"]["confirmed_absent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
