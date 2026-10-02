#!/usr/bin/env python3
"""Verify Hermes fleet unification and generate a Markdown dashboard.

This script is intentionally dependency-light: stdlib + ssh/curl/tailscale commands.
It reads ~/knowledge/60-Mappings/fleet-manifest.yaml when PyYAML is available,
otherwise uses a built-in host fallback matching the manifest.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
KNOWLEDGE = Path(os.environ.get("KNOWLEDGE_DIR", str(HOME / "knowledge"))).expanduser()
OUT = KNOWLEDGE / "60-Mappings" / "Fleet-Status-Dashboard.md"
REPORT_ROOT = Path(os.environ.get("FLEET_REPORT_ROOT", "/nas/fileserver/fleet-reports")).expanduser()
SSH_CONFIG = Path(os.environ.get("SSH_CONFIG", str(HOME / ".ssh/tailscale-config"))).expanduser()

HOSTS = {
    "x1-370": {"ssh": "x1-370", "ip": "100.64.43.123", "os": "linux", "user": "scott", "role": "primary_orchestrator"},
    "xwing": {"ssh": "xwing", "ip": "100.108.99.47", "os": "linux", "user": "scott", "role": "heavy_worker"},
    "deathstar": {"ssh": "deathstar", "ip": "100.78.106.121", "os": "linux", "user": "deathstar", "role": "gpu_specialist"},
    "destroyer": {"ssh": "destroyer", "ip": "100.81.57.77", "os": "linux", "user": "scott", "role": "long_running_worker"},
    "optiplex": {"ssh": "optiplex", "ip": "100.69.158.114", "os": "linux", "user": "scott", "role": "light_worker"},
    "raspberrypi": {"ssh": "raspberrypi", "ip": "100.114.88.89", "os": "linux", "user": "scott", "role": "watcher"},
    "lenovo": {"ssh": "lenovo", "ip": "100.105.137.98", "os": "linux", "user": "scott", "role": "intermittent_worker"},
    "macbook-air": {"ssh": "scottjoyner@scotts-macbook-air", "ip": "100.85.64.117", "os": "macos", "user": "scottjoyner", "role": "macos_soul_source"},
}

# The agent fleet is intentionally separate from the storage fleet. Joyner and
# Beelink are storage/custody authorities even when they are not Hermes agents.
STORAGE_HOSTS = {
    **{k: v for k, v in HOSTS.items() if v["os"] == "linux"},
    "beelink": {"ssh": "scott@100.85.72.121", "ip": "100.85.72.121", "os": "linux", "user": "scott", "role": "nas_storage_owner"},
    "joyner": {"ssh": "joyner@100.83.215.83", "ip": "100.83.215.83", "os": "linux", "user": "joyner", "role": "recovery_storage_owner"},
}

SERVICE_CONTRACTS = {
    "auto-assist": {
        "url": "http://127.0.0.1:8000/health",
        "owner": "auto-assist/compose.production.reconciled.yml",
        "host": "x1-370",
        "profile": "production.reconciled (desired)",
        "bind": "127.0.0.1",
        "port": "8000",
        "tailnet": "no",
        "active_required": "yes",
    },
    "auto-assign": {
        "url": "http://100.64.43.123:8090/health",
        "owner": "auto-assist overlay / auto-assign repository",
        "host": "unknown",
        "profile": "router_plus_assign only",
        "bind": "unknown",
        "port": "8090",
        "tailnet": "no",
        "active_required": "effective-profile-dependent",
    },
    "auto-ingest": {
        "url": "http://100.64.43.123:8766/api/health",
        "owner": "auto-ingest/docker-compose.yml",
        "host": "unknown",
        "profile": "unknown",
        "bind": "unknown",
        "port": "8766",
        "tailnet": "unknown",
        "active_required": "unknown",
    },
    "auto-router": {
        "url": "http://100.64.43.123:8088/health",
        "owner": "auto-router repository",
        "host": "x1-370",
        "profile": "production",
        "bind": "tailnet/reachable",
        "port": "8088",
        "tailnet": "yes",
        "active_required": "yes",
    },
    "lmstudio-x1": {
        "url": "http://100.64.43.123:1234/v1/models",
        "owner": "LM Studio x1-370",
        "host": "x1-370",
        "profile": "production",
        "bind": "tailnet/reachable",
        "port": "1234",
        "tailnet": "yes",
        "active_required": "yes",
    },
    "neo4j-http": {
        "url": "http://100.64.43.123:7474",
        "owner": "Neo4j deployment",
        "host": "x1-370",
        "profile": "bolt-primary",
        "bind": "unknown",
        "port": "7474",
        "tailnet": "no",
        "active_required": "no",
    },
    "sophia-node-3100": {
        "url": "http://100.64.43.123:3100",
        "owner": "Sophia voice deployment",
        "host": "unknown",
        "profile": "unknown",
        "bind": "unknown",
        "port": "3100",
        "tailnet": "unknown",
        "active_required": "unknown",
    },
}
SERVICES = {name: contract["url"] for name, contract in SERVICE_CONTRACTS.items()}


def run(cmd: list[str] | str, timeout: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), text=True, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, f"ERROR: {e}"


def ssh(target: str, remote: str, timeout: int = 12) -> tuple[int, str]:
    base = ["ssh"]
    if SSH_CONFIG.exists():
        base += ["-F", str(SSH_CONFIG)]
    base += ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={min(timeout, 10)}", target, remote]
    return run(base, timeout=timeout)


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def curl_status(url: str) -> tuple[str, str]:
    # Never let a failed request inherit the previous service's response body.
    try:
        Path("/tmp/fleet_verify_body").write_text("")
    except OSError:
        pass
    cmd = ["curl", "-sS", "-o", "/tmp/fleet_verify_body", "-w", "%{http_code}", "--max-time", "5", url]
    code, out = run(cmd, timeout=8)
    status = out.splitlines()[-1] if out else "000"
    if code != 0 and not re.fullmatch(r"\d{3}", status):
        status = "ERR"
    body = ""
    try:
        body = Path("/tmp/fleet_verify_body").read_text(errors="ignore")[:180].replace("\n", " ")
    except Exception:
        pass
    return status, body


def check_host(name: str, info: dict) -> dict:
    target = info["ssh"]
    remote = r'''
printf 'hostname='; hostname 2>/dev/null || scutil --get LocalHostName 2>/dev/null || echo unknown
printf 'user='; id -un
printf 'home='; printf '%s
' "$HOME"
printf 'souls='; find "$HOME/.hermes/souls" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' '
printf 'memory='; test -s "$HOME/.hermes/memories/MEMORY.md" && test -s "$HOME/.hermes/memories/USER.md" && echo ok || echo missing
printf 'fleet_hosts='; test -x "$HOME/bin/fleet-hosts" && echo ok || echo missing
printf 'verify_script='; test -x "$HOME/bin/verify-fleet-unification" && echo ok || echo missing
printf 'knowledge='; test -d "$HOME/knowledge" && echo ok || echo missing
printf 'mcp_config='; if test -f "$HOME/.hermes/config.yaml" && grep -q "mcp_servers:" "$HOME/.hermes/config.yaml" && grep -q "neo4j:" "$HOME/.hermes/config.yaml"; then echo neo4j; elif test -f "$HOME/.hermes/config.yaml"; then echo no_neo4j; else echo no_config; fi
printf 'neo4j_tcp='; python3 - <<'PY'
import socket
try:
 s=socket.create_connection(('100.64.43.123',7687),2); s.close(); print('ok')
except Exception as e: print('fail')
PY
printf 'mounts='; if uname | grep -qi Darwin; then echo n/a; else findmnt -T /nas >/dev/null 2>&1 && echo nas || echo missing; fi
'''
    code, out = ssh(target, remote, timeout=18)
    parsed = {"ssh": "ok" if code == 0 else "fail", "raw": out[:1000]}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            parsed[k.strip()] = v.strip()
    return parsed


def check_storage_host(name: str, info: dict) -> dict:
    """Read-only /nas identity, readability, writeability, and free-space probe."""
    remote = r'''
printf 'hostname='; hostname 2>/dev/null || echo unknown
printf 'nas_source='; findmnt -no SOURCE /nas 2>/dev/null || true
printf 'nas_fstype='; findmnt -no FSTYPE /nas 2>/dev/null || true
printf 'nas_uuid='; findmnt -no UUID /nas 2>/dev/null || true
printf 'nas_target='; findmnt -no TARGET /nas 2>/dev/null || true
printf 'nas_dir='; test -d /nas && echo yes || echo no
printf 'nas_readable='; test -r /nas && echo yes || echo no
printf 'nas_writable='; test -w /nas && echo yes || echo no
printf 'nas_free_bytes='; df -B1 --output=avail /nas 2>/dev/null | tail -1 | tr -d ' ' || true
'''
    code, out = ssh(info["ssh"], remote, timeout=18)
    parsed = {"ssh": "ok" if code == 0 else "fail", "raw": out[:1000]}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            parsed[k.strip()] = v.strip()
    mount = parsed.get("nas_target", "")
    if code != 0:
        parsed["nas_status"] = "TRANSPORT_FAIL"
    elif not mount or parsed.get("nas_dir") != "yes":
        parsed["nas_status"] = "MOUNT_MISSING"
    elif parsed.get("nas_readable") != "yes":
        parsed["nas_status"] = "UNREADABLE"
    else:
        parsed["nas_status"] = "MOUNTED"
    parsed["nas_write_status"] = "WRITABLE" if parsed.get("nas_writable") == "yes" else "READ_ONLY"
    parsed["name"] = name
    return parsed


def latest_backup_hint(host: str) -> str:
    roots = [Path("/nas/agent-state-backups")]
    candidates = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.glob(f"*{host}*/*/runs/*/manifest.txt"):
            try:
                candidates.append((p.stat().st_mtime, p))
            except OSError:
                pass
    if not candidates:
        return "unknown"
    _, p = max(candidates)
    return str(p.parent)


def effective_assistx_profile() -> dict[str, str]:
    """Read the live AssistX container contract without mutating Docker."""
    code, env = run(["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", "assistx-api"], timeout=8)
    if code != 0:
        return {"profile": "UNKNOWN", "bind": "UNKNOWN", "auto_assign_url": "UNKNOWN", "source": "docker inspect unavailable"}
    values = {}
    for line in env.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "profile": values.get("ASSISTX_OVERLAY_MODE", "UNKNOWN"),
        "bind": values.get("ASSISTX_API_BIND", "UNKNOWN"),
        "auto_assign_url": values.get("AUTO_ASSIGN_BASE_URL", ""),
        "source": "assistx-api container environment",
    }


def classify_service(name: str, status: str, body: str, live_profile: dict[str, str]) -> tuple[str, str, str]:
    """Separate transport, application, and profile-aware readiness."""
    contract = SERVICE_CONTRACTS[name]
    if status in {"000", "ERR", "TIMEOUT"} or not status.isdigit():
        transport, application = "TRANSPORT_FAIL", "NOT_PROBED"
    elif not 200 <= int(status) < 300:
        transport, application = "TRANSPORT_OK", f"HTTP_{status}"
    else:
        transport = "TRANSPORT_OK"
        low = body.lower()
        if any(word in low for word in ("degraded", "unhealthy", "critical", "traceback", "error")):
            application = "APPLICATION_DEGRADED"
        elif not body.strip():
            application = "APPLICATION_EMPTY_RESPONSE"
        elif name.startswith("lmstudio-"):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                application = "APPLICATION_INVALID_JSON"
            else:
                models = payload.get("data")
                application = "APPLICATION_OK" if isinstance(models, list) and models else "APPLICATION_NOT_READY"
        else:
            application = "APPLICATION_OK"

    if name == "auto-assist":
        if live_profile.get("bind") == "127.0.0.1" and transport == "TRANSPORT_OK" and application == "APPLICATION_OK":
            readiness = "LOCAL_ONLY_OK"
        elif live_profile.get("bind") == "UNKNOWN":
            readiness = "UNKNOWN"
        else:
            readiness = "PROFILE_DRIFT" if live_profile.get("profile") != "production.reconciled" else "REQUIRED_SERVICE_DEGRADED"
    elif name == "auto-assign":
        if live_profile.get("profile") in {"production.reconciled", "reconciliation", "direct"} or not live_profile.get("auto_assign_url"):
            readiness = "NOT_IN_ACTIVE_PROFILE"
        elif transport == "TRANSPORT_OK" and application == "APPLICATION_OK":
            readiness = "REQUIRED_SERVICE_HEALTHY"
        else:
            readiness = "REQUIRED_SERVICE_MISSING"
    elif name in {"auto-router", "lmstudio-x1"}:
        readiness = "REQUIRED_SERVICE_HEALTHY" if transport == "TRANSPORT_OK" and application == "APPLICATION_OK" else "REQUIRED_SERVICE_DEGRADED"
    elif name == "auto-ingest":
        readiness = "UNKNOWN" if transport != "TRANSPORT_OK" else "UNKNOWN_PROFILE_HEALTHY"
    elif contract["active_required"] == "no":
        readiness = "EXPECTED_INACTIVE_PROFILE"
    else:
        readiness = "UNKNOWN" if transport != "TRANSPORT_OK" else "APPLICATION_HEALTHY_NOT_REQUIRED"
    return transport, application, readiness


def host_blockers(r: dict) -> list[str]:
    blockers = []
    if r.get("ssh") != "ok":
        blockers.append("SSH_TRANSPORT_FAILURE")
    if r.get("memory") != "ok":
        blockers.append("MEMORY_FILES_MISSING")
    if r.get("mcp_config") != "neo4j":
        blockers.append(f"MCP_CONFIG:{r.get('mcp_config', 'unknown')}")
    if r.get("knowledge") != "ok":
        blockers.append("KNOWLEDGE_PATH_MISSING")
    return blockers or ["NONE"]


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_profile = effective_assistx_profile()
    service_rows = []
    for name, url in SERVICES.items():
        status, body = curl_status(url)
        transport, application, readiness = classify_service(name, status, body, live_profile)
        service_rows.append((name, url, status, transport, application, readiness, body))

    agent_rows = []
    for name, info in HOSTS.items():
        r = check_host(name, info)
        r["latest_backup"] = latest_backup_hint(name if name != "macbook-air" else "kipnerter")
        r["blockers"] = host_blockers(r)
        agent_rows.append((name, info, r))

    storage_rows = [(name, info, check_storage_host(name, info)) for name, info in STORAGE_HOSTS.items()]
    neo4j_bolt = "ok" if tcp_open("100.64.43.123", 7687) else "fail"

    lines = ["# Fleet Status Dashboard", "", f"Generated UTC: {now}", ""]
    lines += [
        "## Scope",
        "",
        f"- `AGENT_FLEET`: {len(agent_rows)} nodes (Hermes/agent operating surface)",
        f"- `STORAGE_FLEET`: {len(storage_rows)} nodes (storage/custody surface)",
        "- Joyner and Beelink are intentionally storage-fleet entries, not silently counted as agents.",
        "",
        "## Agent fleet",
        "",
        "| Host | SSH | User | Souls | Memory | Knowledge | Mounts | Neo4j TCP | MCP Config | Blocker reasons | Latest backup hint |",
        "|---|---|---|---:|---|---|---|---|---|---|---|",
    ]
    for name, info, r in agent_rows:
        lines.append("| {name} | {ssh} | {user} | {souls} | {memory} | {knowledge} | {mounts} | {neo4j_tcp} | {mcp_config} | {blockers} | `{backup}` |".format(
            name=name, ssh=r.get("ssh", "?"), user=r.get("user", info.get("user", "?")), souls=r.get("souls", "?"),
            memory=r.get("memory", "?"), knowledge=r.get("knowledge", "?"), mounts=r.get("mounts", "?"),
            neo4j_tcp=r.get("neo4j_tcp", "?"), mcp_config=r.get("mcp_config", "?"),
            blockers="; ".join(r.get("blockers", ["UNKNOWN"])), backup=r.get("latest_backup", "unknown")
        ))

    storage_ok = sum(1 for _, _, r in storage_rows if r.get("nas_status") == "MOUNTED")
    lines += [
        "", "## Storage fleet and /nas", "",
        f"- `/nas` health: `{storage_ok}/{len(storage_rows)} MOUNTED+READABLE` (live probe; not inferred from agent membership)",
        "- `/nas` writeability is reported separately; read-only client mounts are not mislabeled as mount failures.",
        "",
        "| Storage host | SSH | Hostname | /nas health | /nas write | Source | Fstype | UUID | Free bytes |",
        "|---|---|---|---|---|---|---|---|---:|",
    ]
    for name, info, r in storage_rows:
        lines.append(f"| {name} | {r.get('ssh','?')} | {r.get('hostname','?')} | {r.get('nas_status','?')} | {r.get('nas_write_status','?')} | `{r.get('nas_source','')}` | {r.get('nas_fstype','')} | {r.get('nas_uuid','')} | {r.get('nas_free_bytes','?')} |")

    lines += [
        "", "## Services", "",
        f"- Effective AssistX profile: `{live_profile.get('profile', 'UNKNOWN')}` ({live_profile.get('source', 'unknown')})",
        f"- Effective AssistX bind: `{live_profile.get('bind', 'UNKNOWN')}`",
        f"- Effective AUTO_ASSIGN_BASE_URL: `{live_profile.get('auto_assign_url', 'UNKNOWN') or '(empty)'}`",
        "- Desired reconciled production overlay is not inferred as active; live container environment is authoritative.",
        "",
        "| Service | Canonical owner | Host | Profile | Bind | Port | Tailnet | Required | Probe URL | HTTP | Transport | Application | Readiness | Response preview |",
        "|---|---|---|---|---|---:|---|---|---|---:|---|---|---|---|",
    ]
    for name, url, status, transport, application, readiness, body in service_rows:
        contract = SERVICE_CONTRACTS[name]
        safe_body = body.replace("|", "\\|")
        lines.append(f"| {name} | `{contract['owner']}` | {contract['host']} | {contract['profile']} | {contract['bind']} | {contract['port']} | {contract['tailnet']} | {contract['active_required']} | `{url}` | {status} | {transport} | {application} | {readiness} | {safe_body} |")

    control_plane = {
        name: readiness
        for name, _url, _status, _transport, _application, readiness, _body in service_rows
        if name in {"auto-assist", "auto-assign", "auto-ingest", "auto-router"}
    }
    required_services = ["auto-assist", "auto-router"]
    if live_profile.get("profile") == "router_plus_assign" and live_profile.get("auto_assign_url"):
        required_services.append("auto-assign")
    required_ready = {"REQUIRED_SERVICE_HEALTHY", "LOCAL_ONLY_OK"}
    control_plane_ready = all(control_plane.get(name) in required_ready for name in required_services)
    lines += [
        "", "## Control-plane readiness", "",
        f"- Overall fleet operational readiness: `{'READY' if control_plane_ready else 'DEGRADED'}`",
        "- This summary is separate from host-local bootstrap blockers; host `NONE` does not imply fleet operational readiness.",
    ]
    for name in ("auto-assist", "auto-assign", "auto-ingest", "auto-router"):
        lines.append(f"- `{name}`: `{control_plane.get(name, 'NOT_PROBED')}`")

    lines += [
        "", "## Service/fleet summary", "",
        f"- Neo4j Bolt transport: `{neo4j_bolt}`",
        "- Service transport and application classifications are independent; an HTTP 200 with a degraded body or empty LM Studio model list is not reported ready.",
        "- Response buffers are cleared before every service probe; a failed probe cannot reuse a prior service preview.",
        "", "## Agent operating rules", "",
        "- Start by reading `~/knowledge/Home.md` and `~/knowledge/60-Mappings/fleet-manifest.yaml`.",
        "- Use Neo4j MCP tools when available.",
        "- Do not copy secrets or provider keys through the vault/soul sync path.",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = "\n".join(lines) + "\n"
    OUT.write_text(report)
    try:
        if not REPORT_ROOT.is_relative_to(Path("/nas")):
            raise RuntimeError(f"refusing non-/nas report root: {REPORT_ROOT}")
        if not (Path("/nas") / "fileserver").exists():
            raise RuntimeError("/nas/fileserver is unavailable")
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        (REPORT_ROOT / f"fleet-status-{now.replace(':','').replace('-','')}.md").write_text(report)
    except Exception as exc:
        print(f"ERROR: persistent fleet report not written: {exc}", file=sys.stderr)
        return 2
    print(str(OUT))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
