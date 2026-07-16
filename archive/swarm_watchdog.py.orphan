#!/usr/bin/env python3
"""swarm_watchdog.py — observe failures and validate that completed swarm work
actually produced something.

Three jobs:
  1. Failure observation  — eval-registry anomalies, FAILED AssistX tasks, down nodes.
  2. Completion validation — for DONE tasks, detect trivial/empty outputs and
     referenced files that were never written (real "did it finish?" checking).
  3. Self-task artifact check — for self:* successes, confirm a knowledge artifact
     was actually produced, not just a clean hermes exit.

Usage:
  python3 swarm_watchdog.py report
  python3 swarm_watchdog.py validate [--limit N] [--deep]
  python3 swarm_watchdog.py watch [--interval S]

Exit code 2 when critical issues are found (for cron/alerting).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ASSISTX_URL = os.getenv("ASSISTX_URL", "http://localhost:8000").rstrip("/")
ASSISTX_USER = os.getenv("ASSISTX_USER", "admin")
ASSISTX_PASS = os.getenv("ASSISTX_PASS", "change-me")
ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8088").rstrip("/")
EVAL_PATH = os.getenv("HERMES_EVAL_PATH", "/media/scott/SSD_4TB/knowledge/model-profiles.json")
KNOWLEDGE_ROOT = os.getenv("KNOWLEDGE_ROOT", "/media/scott/SSD_4TB/knowledge")

# Cloud brokers that are intentionally unconfigured in this fleet — don't alert.
IGNORE_PROVIDERS = {"cerebras", "groq", "openrouter"}

SUCCESS_RATE_WARN = float(os.getenv("WATCHDOG_RATE_WARN", "0.85"))
SUCCESS_RATE_CRIT = float(os.getenv("WATCHDOG_RATE_CRIT", "0.6"))

# Outputs that look like the agent said "done" without doing the work.
TRIVIAL_PATTERNS = [
    re.compile(r"done\s*[-–]\s*i[’']?ve completed", re.I),
    re.compile(r"i(?: have|'ve) completed that for you", re.I),
    re.compile(r"let me know if you need (?:any|anything)", re.I),
    re.compile(r"^done\.?$", re.I),
    re.compile(r"i'?ve (?:got it|taken care of it)", re.I),
]
# File-path references we can verify exist on disk. Require a real directory chain
# ending in a known extension so we don't flag words like "/pattern" or "/smoke".
PATH_RE = re.compile(
    r"(?:(?:/~|~|/|\./)?(?:[\w.\-]+/)+[\w.\-]+\."
    r"(?:md|txt|py|json|yaml|yml|toml|csv|log|sh|cfg|ini))"
)

CRITICAL: List[str] = []
WARNING: List[str] = []


def _get_json(url: str, timeout: int = 10) -> Optional[Any]:
    req = urllib.request.Request(url)
    if ASSISTX_USER:
        import base64
        token = base64.b64encode(f"{ASSISTX_USER}:{ASSISTX_PASS}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        return {"_error": str(e)}


def _load_eval() -> Dict[str, Any]:
    try:
        with open(EVAL_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
            d.setdefault("models", {})
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        return {"models": {}}


# ---------------------------------------------------------------------------
# 1. Failure observation
# ---------------------------------------------------------------------------
def observe_eval_failures(eval_data: Dict[str, Any]) -> None:
    from collections import Counter

    for model, info in eval_data.get("models", {}).items():
        for cat, t in info.get("tasks", {}).items():
            runs = t.get("runs", 0)
            if not runs:
                continue
            rate = t.get("success_rate", 1.0)
            fails = runs - t.get("success", 0)
            # failure-kind breakdown from the ring buffer
            kinds = Counter(f.get("kind") for f in t.get("recent_failures", []) if f.get("kind"))
            kind_str = ("; kinds: " + ", ".join(f"{k}={n}" for k, n in kinds.most_common())) if kinds else ""
            if rate < SUCCESS_RATE_CRIT:
                CRITICAL.append(
                    f"[eval] {model} / {cat}: success_rate={rate} ({t.get('success')}/{runs}), "
                    f"avg={t.get('avg_seconds')}s{kind_str}"
                )
            elif rate < SUCCESS_RATE_WARN:
                WARNING.append(
                    f"[eval] {model} / {cat}: success_rate={rate} ({t.get('success')}/{runs}){kind_str}"
                )
            trivial_n = t.get("trivial")
            if trivial_n:
                WARNING.append(
                    f"[eval] {model} / {cat}: {trivial_n} run(s) exited cleanly but produced "
                    f"trivial/empty output (false-success, not real work)"
                )
            if t.get("last_failure"):
                clue = f" last_error={t['last_error'][:60]}" if t.get("last_error") else ""
                WARNING.append(f"[eval] {model} / {cat}: last failure at {t['last_failure']}{clue}")


def observe_assistx_failures(limit: int = 20) -> None:
    data = _get_json(f"{ASSISTX_URL}/api/tasks?status=FAILED&limit={limit}")
    if isinstance(data, dict) and data.get("_error"):
        WARNING.append(f"[assistx] cannot reach task API: {data['_error']}")
        return
    items = (data or {}).get("items", [])
    for t in items:
        summary = t.get("result_summary") or ""
        err = ""
        rj = t.get("result_json")
        if isinstance(rj, str):
            try:
                rj = json.loads(rj)
            except json.JSONDecodeError:
                rj = {}
        if isinstance(rj, dict):
            err = rj.get("error", "")
        CRITICAL.append(
            f"[assistx:FAILED] {t.get('id')} '{t.get('title','')[:50]}': {summary[:80]} {err}"
        )


def observe_down_nodes() -> None:
    data = _get_json(f"{ROUTER_URL}/admin/live-models")
    if isinstance(data, dict) and data.get("_error"):
        WARNING.append(f"[router] cannot reach live-models: {data['_error']}")
        return
    for p in data.get("provider_health", []):
        provider = p.get("provider", "")
        if not p.get("ok"):
            if provider in IGNORE_PROVIDERS:
                continue
            CRITICAL.append(f"[router:DOWN] {provider}: {p.get('error','')}")


# ---------------------------------------------------------------------------
# 2 + 3. Completion validation
# ---------------------------------------------------------------------------
def _output_of(task: Dict[str, Any]) -> str:
    rj = task.get("result_json")
    if isinstance(rj, str):
        try:
            rj = json.loads(rj)
        except json.JSONDecodeError:
            rj = {}
    if isinstance(rj, dict) and rj.get("output"):
        return rj["output"]
    return task.get("result_summary") or ""


def _is_trivial(output: str) -> bool:
    if not output or len(output.strip()) < 15:
        return True
    return any(p.search(output) for p in TRIVIAL_PATTERNS)


def _check_paths(output: str) -> List[str]:
    missing = []
    for m in PATH_RE.findall(output):
        path = os.path.expanduser(m)
        if path.startswith("~"):
            path = os.path.expanduser(path)
        if not os.path.exists(path):
            missing.append(m)
    return missing


def _self_artifact_present(model: str, completed_at_ts: Optional[int]) -> Tuple[bool, str]:
    """Best-effort: did a knowledge artifact appear after a self-task success?"""
    model_dir = os.path.join(KNOWLEDGE_ROOT, model.replace("/", "_"))
    candidates = []
    # model-specific scratch dir should have grown beyond its ENV.md
    if os.path.isdir(model_dir):
        extras = [f for f in os.listdir(model_dir) if f != "ENV.md"]
        if extras:
            return True, f"{model_dir}: {extras[:3]}"
    # or any fresh TOP-LEVEL markdown appeared in the vault after completion
    # (hermes session transcripts live in subdirs and must NOT count).
    if completed_at_ts:
        cutoff = completed_at_ts - 60
        for f in os.listdir(KNOWLEDGE_ROOT):
            if not f.endswith(".md") or f in ("Home.md", "README.md", "VAULT_INDEX.md", "ENV.md"):
                continue
            fp = os.path.join(KNOWLEDGE_ROOT, f)
            if not os.path.isfile(fp):
                continue
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            if mtime >= cutoff:
                return True, f
    return False, "no artifact found"


def validate_completed(limit: int = 20, deep: bool = False) -> List[Dict[str, Any]]:
    data = _get_json(f"{ASSISTX_URL}/api/tasks?status=DONE&limit={limit}")
    if isinstance(data, dict) and data.get("_error"):
        WARNING.append(f"[assistx] cannot reach task API: {data['_error']}")
        return []
    items = (data or {}).get("items", [])
    findings = []
    trivial_count = 0
    for t in items:
        tid = t.get("id")
        title = t.get("title", "")
        output = _output_of(t)
        issues = []
        detail_ref = ""
        cat = (t.get("kind") or "").lower()
        is_self = "self" in cat or "bulk" in cat
        if is_self:
            # Self/bulk tasks write a file; the chat reply is a short confirmation,
            # so judge the artifact, not the reply text.
            ok, detail = _self_artifact_present(str(t.get("completed_by", "")), t.get("completed_at_ts"))
            if not ok:
                issues.append("self-task success but no artifact produced")
            else:
                detail_ref = detail
        else:
            if _is_trivial(output):
                issues.append("trivial/empty output (likely not actually completed)")
            missing = _check_paths(output)
            if missing:
                issues.append(f"referenced paths missing: {missing[:3]}")
        if issues:
            joined = " ".join(issues)
            sev = "CRITICAL" if "trivial" in joined or "no artifact" in joined else "WARNING"
            if "trivial" in joined:
                trivial_count += 1
            msg = f"[{sev}][assistx:DONE] {tid} '{title[:50]}': {'; '.join(issues)}"
            (CRITICAL if sev == "CRITICAL" else WARNING).append(msg)
            findings.append({"id": tid, "title": title, "issues": issues, "output": output[:300]})
        elif detail_ref:
            findings.append({"id": tid, "title": title, "artifact": detail_ref})
    if trivial_count:
        WARNING.append(
            f"[validate] {trivial_count} DONE task(s) had trivial outputs — the eval "
            f"success_rate likely OVERSTATES real completion (hermes exited 0 but did no work)"
        )
    return findings


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report() -> None:
    print("=" * 70)
    print(f"SWARM WATCHDOG  {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    print("\n-- Failure observation --")
    if CRITICAL:
        print("CRITICAL:")
        for c in CRITICAL:
            print("  •", c)
    if WARNING:
        print("WARNINGS:")
        for w in WARNING:
            print("  •", w)
    if not CRITICAL and not WARNING:
        print("  (none)")
    print("\n-- Eval registry summary --")
    ev = _load_eval()
    for model, info in ev.get("models", {}).items():
        tasks = info.get("tasks", {})
        tot = sum(t.get("runs", 0) for t in tasks.values())
        ok = sum(t.get("success", 0) for t in tasks.values())
        print(f"  {model}: tier={info.get('tier')} env_cfg={info.get('environment_configured')} "
              f"runs={tot} ok={ok} artifacts_dir={'yes' if os.path.isdir(os.path.join(KNOWLEDGE_ROOT, model.replace('/','_'))) else 'no'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    pv = sub.add_parser("validate")
    pv.add_argument("--limit", type=int, default=20)
    pv.add_argument("--deep", action="store_true")
    pw = sub.add_parser("watch")
    pw.add_argument("--interval", type=int, default=300)

    args = ap.parse_args()
    eval_data = _load_eval()

    if args.cmd == "report":
        observe_eval_failures(eval_data)
        observe_assistx_failures()
        observe_down_nodes()
        print_report()
    elif args.cmd == "validate":
        observe_eval_failures(eval_data)
        observe_down_nodes()
        observe_assistx_failures()
        findings = validate_completed(limit=args.limit, deep=args.deep)
        print_report()
        print("\n-- Completed-work validation --")
        if not findings:
            print("  no anomalies in sampled DONE tasks")
        else:
            for f in findings:
                print(f"  {f.get('id')}: {f.get('title','')[:50]}")
                if f.get("issues"):
                    for i in f["issues"]:
                        print("     -", i)
                if f.get("artifact"):
                    print("     + artifact:", f["artifact"])
    elif args.cmd == "watch":
        interval = args.interval
        while True:
            CRITICAL.clear(); WARNING.clear()
            observe_eval_failures(_load_eval())
            observe_assistx_failures()
            observe_down_nodes()
            validate_completed(limit=20)
            ts = datetime.now(timezone.utc).isoformat()
            if CRITICAL or WARNING:
                print(f"[{ts}] ALERTS: {len(CRITICAL)} critical, {len(WARNING)} warning")
                print_report()
            else:
                print(f"[{ts}] ok")
            time.sleep(interval)

    return 2 if CRITICAL else 0


if __name__ == "__main__":
    sys.exit(main())
