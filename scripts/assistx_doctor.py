#!/usr/bin/env python3
"""AssistX deployment-conformance doctor — versioned copy (P0-C, 2026-11-26).

Compares RUNNING state against DECLARED state across the fleet's control-plane
services and writes structured findings for the control room. Born from the
2026-08-24 incidents: stale images, missing rotated creds, loopback-only
binds, expired projections - each silent for hours.

Report: FLEET-STATE/doctor-report.json  (served at /api/doctor/report)
Exit code: 0 = healthy/warnings only, 1 = any FAIL finding.

P0-C changes over the unversioned 258-line runner:
  * Compose reproducibility: registered config-file paths (provenance from
    ``com.docker.compose.*`` labels, or a JSON snapshot) are checked on disk.
    A project that registers a missing file now yields a FAIL finding, instead
    of the previous healthy 0/0 blind spot.
  * No hardcoded project directories: the unused ``project`` entries and the
    absolute alert import path were removed. Provenance is supplied by the
    caller/manifest; ``ASSISTX_SRC_DIR`` (or the repo-relative ``src/``) supplies
    the alert module when installed. ``~/bin`` should be a thin wrapper only.

Compose manifest JSON (path via ``--compose-manifest`` or
``ASSISTX_COMPOSE_MANIFEST``)::

    {"projects": {"project-name": ["/path/docker-compose.yml", ...]}}

Absent a manifest, the compose check emits nothing: enabling it in production
is gated on canonical project naming (P0-B / G2). Tests inject synthetic
manifests and stub probes, so no docker, HTTP, DB, credential or mount access
is needed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FLEET_STATE = Path(os.getenv(
    "ASSISTX_FLEET_STATE_DIR",
    "/media/scott/SSD_4TB/hermes-home/FLEET-STATE",
))
OUT_PATH = FLEET_STATE / "doctor-report.json"
COMPOSE_MANIFEST_ENV = "ASSISTX_COMPOSE_MANIFEST"

# Module-level CLI state (set in main(); kept as globals to preserve the
# signature and report shape consumed by doctor_routes.py / the control room).
SKIP_COMPOSE_CHECK = False
COMPOSE_PROJECT_FILTER = ""

# (container, required_env_checks{VAR: forbid_value_or_None}).
# Project provenance is deliberately NOT hardcoded here: it comes from compose
# labels or the manifest, so stale checkout paths cannot drift into the doctor.
CONTAINER_ENV_CHECKS = {
    "assistx-api": {
        "forbid": {"BASIC_AUTH_PASS": ["change-me"]},
        "require": ["AUTO_ROUTER_ADMIN_TOKEN", "ASSISTX_EXECUTOR_DEFAULT_MODEL_ALIASES",
                    "ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS"],
    },
    "assistx-hermes-adapter": {
        "forbid": {"ASSISTX_PASS": ["change-me", "gluhlaf8"]},
        "require": ["HERMES_SELFTASK_ENABLED", "ASSISTX_EXECUTOR_DEFAULT_MODEL_ALIASES"],
    },
    "auto-assign": {
        "forbid": {},
        "require": ["AUTO_ASSIGN_API_TOKEN", "AUTO_ROUTER_ADMIN_TOKEN"],
    },
}

ENDPOINT_PROBES = [
    ("assistx-api", "http://localhost:8000/health"),
    ("auto-router", "http://localhost:8088/health"),
    ("auto-assign", "http://localhost:8090/health"),
    ("lmstudio-x1", "http://localhost:1234/v1/models"),
    # sophia-voice REMOVED 2026-10-05. It has no systemd unit and no compose
    # entry anywhere, so nothing can start it; the probe had been failing
    # "connection refused" and dragging overall=unhealthy on every run, which
    # masked the real findings. Restore this row only if the service comes back.
]

FRESHNESS_CHECKS = [
    # 2026-10-20 operator-approved repair: the previous single entry pointed at
    # the legacy ~/neo4j-online-backups dir (no feeder since the pipeline moved
    # staging->/nas), mislabeled 'runtime projection approval'. Replaced with
    # the two real roots: the live-admission renewal evidence (refreshed every
    # 5 min by the keepalive) and the verified off-host backup destination.
    #
    # The fourth field is the PRODUCER of that root (a systemd user timer), or
    # None. It exists because a lane can be stale for a reason that is not a
    # fault: the operator's recovery hold disables the producer on purpose, and
    # then every freshness check reports the resulting age as if the backup had
    # failed. It had not - it was paused by order. A check that cannot tell
    # "the schedule is paused by the hold" from "the schedule stopped running"
    # turns a designed state into a daily red, and a daily red nobody can act
    # on is the same as no red at all.
    ("runtime projection approval",
     "/home/scott/.local/state/assistx/approvals", 36 * 3600, None),
    ("neo4j online backup", "/nas/neo4j-online-backups", 30 * 3600,
     "neo4j-online-backup.timer"),
]

HOLD_CONF = Path.home() / ".config/fleet/recovery-hold.conf"


def held_producers() -> tuple[set[str], str]:
    """(held timer names, hold reason) from the recovery hold file, if it is in force."""
    try:
        text = HOLD_CONF.read_text()
    except OSError:
        return set(), ""
    held, reason = set(), ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("HELD_TIMERS="):
            # The value is quoted in the file (HELD_TIMERS="a.timer b.timer"), so
            # the quotes have to come off before the names are split - otherwise
            # the first name keeps its leading quote and never matches.
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            held = {n for n in value.split() if n}
        elif line.startswith("HOLD_REASON="):
            reason = line.split("=", 1)[1].strip()
    return held, reason


HTTP_TIMEOUT = 6.0


def http_ok(url: str, timeout: float = None) -> tuple[bool, str]:
    if timeout is None:
        timeout = HTTP_TIMEOUT
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except Exception as exc:
        return False, str(exc)[:120]


def compose_projects_from_labels(rows) -> dict[str, list[str]]:
    """Turn ``com.docker.compose.*`` label rows into project -> config files.

    ``rows`` is any iterable of label mappings (as returned by a read-only
    ``docker inspect`` collector). The config-files label is colon-separated.
    Pure function: no docker call is made here.
    """
    projects: dict[str, set[str]] = {}
    for labels in rows:
        name = labels.get("com.docker.compose.project")
        if not name:
            continue
        raw = labels.get("com.docker.compose.project.config_files") or ""
        files = [part for part in str(raw).split(":") if part]
        projects.setdefault(str(name), set()).update(files)
    return {name: sorted(files) for name, files in projects.items()}


def load_compose_projects(path=None) -> tuple[dict[str, list[str]], str]:
    """Load (project -> config files, error) from a provenance JSON snapshot.

    Returns ``({}, "")`` when no manifest is configured, so an unconfigured
    doctor keeps its previous report shape. An explicitly configured but
    unreadable manifest is reported as an error rather than silently ignored -
    silence is the blind spot this check exists to close.
    """
    source = path if path is not None else os.getenv(COMPOSE_MANIFEST_ENV, "")
    if not source:
        return {}, ""
    snapshot = Path(source)
    try:
        data = json.loads(snapshot.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"unreadable compose provenance manifest {snapshot}: {exc}"
    projects = data.get("projects", data) if isinstance(data, dict) else {}
    if not isinstance(projects, dict):
        return {}, f"compose provenance manifest {snapshot} has no 'projects' mapping"
    return {
        str(name): [str(f) for f in (files or [])]
        for name, files in projects.items()
    }, ""


def compose_findings(projects, only_project=None) -> list[dict]:
    """Report a FAIL for every registered project whose config files are missing."""
    if only_project:
        if only_project not in projects:
            return [{
                "check": f"compose:{only_project}:config-files", "status": "fail",
                "detail": "registered compose project not found in provenance manifest",
            }]
        selected = {only_project: projects[only_project]}
    else:
        selected = projects

    out: list[dict] = []
    for name in sorted(selected):
        files = [Path(f) for f in selected[name]]
        missing = [str(f) for f in files if not f.is_file()]
        if not files:
            status, detail = "fail", "project registers no config files"
        elif missing:
            status, detail = "fail", (
                f"registered config file(s) missing on disk: {', '.join(missing)}"
            )
        else:
            status, detail = "healthy", f"{len(files)} registered config file(s) present"
        out.append({
            "check": f"compose:{name}:config-files", "status": status, "detail": detail,
        })
    return out


def findings(*, compose_manifest=None, http_probe=None, docker_env=None,
             freshness_checks=None, held=None) -> list[dict]:
    """Collect findings. All external inputs are injectable for offline tests.

    ``http_probe`` defaults to :func:`http_ok`; ``docker_env`` maps container
    name to an env mapping (``None`` means run the live ``docker exec`` probe);
    ``freshness_checks`` defaults to :data:`FRESHNESS_CHECKS`; ``held`` defaults
    to :func:`held_producers`. Tests pass synthetic values for all four, so no
    live service, DB, credential file or network mount is touched.
    """
    out: list[dict] = []
    probe = http_probe or http_ok

    # --- endpoint reachability (parallel) ---
    endpoint_results = {}

    def _probe(name, url):
        ok, detail = probe(url)
        endpoint_results[name] = (ok, detail)

    threads = [threading.Thread(target=_probe, args=(n, u), daemon=True) for n, u in ENDPOINT_PROBES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for name, url in ENDPOINT_PROBES:
        ok, detail = endpoint_results[name]
        out.append({
            "check": f"endpoint:{name}", "status": "healthy" if ok else "fail",
            "detail": detail,
        })

    # --- container env contracts ---
    for cname, cfg in CONTAINER_ENV_CHECKS.items():
        if docker_env is not None:
            if cname not in docker_env:
                continue
            env = docker_env[cname]
        else:
            try:
                raw = subprocess.run(
                    ["docker", "exec", cname, "env"], capture_output=True, text=True,
                    timeout=10, check=True,
                ).stdout
            except Exception as exc:
                out.append({"check": f"env:{cname}", "status": "warn",
                            "detail": f"container unreachable: {exc}"})
                continue
            env = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        for var, banned in cfg.get("forbid", {}).items():
            val = env.get(var)
            if var not in env:
                out.append({"check": f"env:{cname}.{var}", "status": "fail",
                            "detail": "missing from container env"})
            elif val in banned:
                out.append({"check": f"env:{cname}.{var}", "status": "fail",
                            "detail": f"value matches banned default ({val[:12]}...)"})
        for var in cfg.get("require", []):
            if not env.get(var, "").strip():
                out.append({"check": f"env:{cname}.{var}", "status": "fail",
                            "detail": "required value empty/missing"})

    # --- credential / evidence freshness ---
    held_timers, hold_reason = held if held is not None else held_producers()
    checks = FRESHNESS_CHECKS if freshness_checks is None else freshness_checks
    for label, root, max_age_s, producer in checks:
        p = Path(root)
        try:
            newest = max((f.stat().st_mtime for f in p.rglob("*") if f.is_file()), default=0)
        except OSError:
            continue
        age_h = (time.time() - newest) / 3600
        status = "healthy" if age_h <= max_age_s / 3600 else "warn"
        detail = f"newest artifact {age_h:.1f}h old (limit {max_age_s/3600:.0f}h)"
        if status == "warn" and producer and producer in held_timers:
            # The producer is held by the operator's recovery hold, so the age is
            # the order's consequence, not a failed backup. Report it as paused -
            # visible, but not counted as a fault. The hold's own lift procedure
            # names the gap-closer run that catches the lane up.
            status = "paused"
            detail = (f"PAUSED by the recovery hold ({hold_reason or 'hold active'}): "
                      f"producer {producer} is held, so the {age_h:.1f}h age is the "
                      f"order's consequence, not a failed backup. The hold's lift "
                      f"procedure runs the gap-closer for this lane first.")
        out.append({"check": f"freshness:{label}", "status": status, "detail": detail})

    # --- runtime projection served fresh by assistx ---
    ok, detail = probe("http://localhost:8000/api/router/context-projection")
    out.append({"check": "projection:context-projection", "status": "healthy" if ok else "fail",
                "detail": detail})

    # --- compose reproducibility (P0-C) ---
    if not SKIP_COMPOSE_CHECK:
        projects, manifest_error = load_compose_projects(compose_manifest)
        if manifest_error:
            out.append({"check": "compose:manifest", "status": "fail", "detail": manifest_error})
        else:
            out.extend(compose_findings(projects, only_project=COMPOSE_PROJECT_FILTER or None))

    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AssistX deployment-conformance doctor")
    parser.add_argument("--version", action="version", version="assistx-doctor 1.2 (P0-C versioned)")
    parser.add_argument("--json", action="store_true", help="Print report JSON to stdout")
    parser.add_argument("--filter", dest="flt", default="", help="Substring filter on check name")
    parser.add_argument("--quiet", action="store_true", help="Suppress human summary")
    parser.add_argument("--out", default=None, help="Override output path")
    parser.add_argument("--timeout", type=float, default=6.0, help="HTTP probe timeout seconds")
    parser.add_argument("--checks", default="", help="Comma-separated check-name substrings to include")
    parser.add_argument("--list-checks", action="store_true", help="Print check names and exit")
    parser.add_argument("--compose-project", default="",
                        help="Restrict the compose check to one registered project")
    parser.add_argument("--skip-compose-check", action="store_true",
                        help="Disable the compose reproducibility check")
    parser.add_argument("--compose-manifest", default=None,
                        help=f"Compose provenance JSON (else ${COMPOSE_MANIFEST_ENV})")
    args = parser.parse_args(argv)

    global HTTP_TIMEOUT, SKIP_COMPOSE_CHECK, COMPOSE_PROJECT_FILTER
    HTTP_TIMEOUT = args.timeout
    SKIP_COMPOSE_CHECK = args.skip_compose_check
    COMPOSE_PROJECT_FILTER = args.compose_project

    FLEET_STATE.mkdir(parents=True, exist_ok=True)
    findings_list = findings(compose_manifest=args.compose_manifest)
    if args.list_checks:
        for c in sorted({f.get("check", "") for f in findings_list}):
            print(c)
        return 0
    if args.flt:
        findings_list = [f for f in findings_list if args.flt in f.get("check", "")]
    if args.checks:
        wanted = [c.strip() for c in args.checks.split(",") if c.strip()]
        findings_list = [f for f in findings_list if any(w in f.get("check", "") for w in wanted)]
    fails = sum(1 for f in findings_list if f["status"] == "fail")
    warns = sum(1 for f in findings_list if f["status"] == "warn")
    overall = "unhealthy" if fails else ("degraded" if warns else "healthy")
    report = {
        "generated_at_ms": int(time.time() * 1000),
        "overall": overall,
        "counts": {"fail": fails, "warn": warns, "total": len(findings_list)},
        "findings": findings_list,
    }
    out_path = Path(args.out) if args.out else OUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1))
    if args.json:
        print(json.dumps(report, indent=1))
    elif not args.quiet:
        print(f"doctor: overall={overall} fail={fails} warn={warns} -> {out_path}")
    # Alert on failures via Pushcut. The import root is repo-relative (or
    # ASSISTX_SRC_DIR for a thin wrapper install), never a hardcoded checkout.
    if fails > 0:
        try:
            src_dir = Path(os.getenv(
                "ASSISTX_SRC_DIR",
                str(Path(__file__).resolve().parents[1] / "src"),
            ))
            sys.path.insert(0, str(src_dir))
            from assistx.alerts import pushcut_notify
            failed = [f for f in findings_list if f["status"] == "fail"]
            detail = "; ".join(f"{f['check']}: {f['detail'][:60]}" for f in failed[:3])
            pushcut_notify(
                f"AssistX Doctor: {fails} failure(s)",
                detail
            )
        except Exception:
            pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
