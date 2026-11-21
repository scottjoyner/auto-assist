#!/usr/bin/env python3
"""Fail-closed, read-only AssistX router migration preflight.

Runs a fixed, status-only DNS/TCP/GET health probe within the already-running
AssistX API container. Never accesses operator credentials, admin routes,
request bodies, Docker env output, or production write APIs. Does not change
container environment, Compose files, network policy, or container lifecycle.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

SCHEMA = "assistx.router-internal-alias-preflight.v1"
CONTAINER = "assistx-api"
HERMES_CONTAINER = "assistx-hermes-adapter"
ROUTER_ALIAS = "llm-router"
ROUTER_PORT = 8088
SETTINGS = ("AUTO_ROUTER_BASE_URL", "FLEET_ROUTER_URL")
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ROOT = Path(__file__).resolve().parents[1]
IDENTITY_TEMPLATE = "{{.Image}}|{{.State.StartedAt}}|{{.State.Running}}"

# Code is fixed and gets no untrusted arguments or auth tokens.
# It deliberately does not print actual URL values, usernames, paths or query.
# Health is a public, body-free status probe, NOT an admin/auth verification.
RUNTIME_PROBE = """
import http.client,json,os,socket,urllib.parse
keys=('AUTO_ROUTER_BASE_URL','FLEET_ROUTER_URL')
settings={}
for key in keys:
    raw=os.environ.get(key,'').strip()
    try:
        u=urllib.parse.urlsplit(raw)
        canonical=bool(raw and u.scheme=='http' and u.hostname=='llm-router'
                       and u.port==8088 and u.path in ('','/') and
                       not u.username and not u.password and
                       not u.query and not u.fragment)
        userinfo=bool(u.username or u.password)
    except ValueError:
        canonical=False
        userinfo=True
    settings[key]={'configured':bool(raw),'canonical_internal_router_url':canonical,
                   'embedded_userinfo':userinfo}
dns=False
tcp=False
status=None
try:
    dns=bool(socket.getaddrinfo('llm-router',8088,0,socket.SOCK_STREAM))
    if dns:
        c=socket.create_connection(('llm-router',8088),timeout=2)
        c.close()
        tcp=True
except (OSError,TimeoutError):
    pass
if tcp:
    try:
        c=http.client.HTTPConnection('llm-router',8088,timeout=2)
        c.request('GET','/health',headers={'Connection':'close'})
        status=c.getresponse().status
        c.close()
    except (OSError,TimeoutError,http.client.HTTPException):
        status=None
print(json.dumps({
    'settings':settings,
    'router_alias_dns_resolves':dns,
    'router_alias_tcp_reachable':tcp,
    'router_alias_health_http_status':status,
    'credentials_or_headers_sent':False,
    'response_bodies_read':False,
    'production_mutation':False,
},sort_keys=True))
"""


# The Hermes adapter consumes the router's /v1 inference API, not the
# AssistX API's two URLs. Closing a host-published router port before
# migrating this independent consumer would strand Hermes.
HERMES_PROBE = """
import http.client,json,os,socket,urllib.parse
raw=os.environ.get('HERMES_LM_BASE_URL','').strip()
try:
    u=urllib.parse.urlsplit(raw)
    canonical=bool(raw and u.scheme=='http' and u.hostname=='llm-router'
                   and u.port==8088 and u.path=='/v1'
                   and not u.username and not u.password
                   and not u.query and not u.fragment)
    userinfo=bool(u.username or u.password)
except ValueError:
    canonical=False
    userinfo=True
dns=False
tcp=False
status=None
try:
    dns=bool(socket.getaddrinfo('llm-router',8088,0,socket.SOCK_STREAM))
    if dns:
        c=socket.create_connection(('llm-router',8088),timeout=2)
        c.close()
        tcp=True
except (OSError,TimeoutError):
    pass
if tcp:
    try:
        c=http.client.HTTPConnection('llm-router',8088,timeout=2)
        c.request('GET','/health',headers={'Connection':'close'})
        status=c.getresponse().status
        c.close()
    except (OSError,TimeoutError,http.client.HTTPException):
        status=None
print(json.dumps({
    'configured':bool(raw),
    'canonical_internal_router_v1_url':canonical,
    'embedded_userinfo':userinfo,
    'router_alias_dns_resolves':dns,
    'router_alias_tcp_reachable':tcp,
    'router_alias_health_http_status':status,
    'credentials_or_headers_sent':False,
    'response_bodies_read':False,
    'production_mutation':False,
},sort_keys=True))
"""


def docker_read(argv: list[str]) -> str:
    identities = (
        ["docker", "inspect", "--format", IDENTITY_TEMPLATE, container]
        for container in (CONTAINER, HERMES_CONTAINER)
    )
    probes = (
        ["docker", "exec", CONTAINER, "python", "-B", "-c", RUNTIME_PROBE],
        ["docker", "exec", HERMES_CONTAINER, "python", "-B", "-c", HERMES_PROBE],
    )
    if argv not in (*identities, *probes):
        raise ValueError("only exact read-only identity and fixed API probe allowed")
    child = subprocess.run(
        argv, capture_output=True, text=True,
        check=False, timeout=10,
    )
    if child.returncode or len(child.stdout) > 2048:
        raise RuntimeError("bounded Docker read-only probe unavailable")
    return child.stdout.strip()


def container_identity(read=docker_read, *, container: str = CONTAINER) -> str:
    if container not in (CONTAINER, HERMES_CONTAINER):
        raise ValueError("unapproved Docker container identity")
    raw = read([
        "docker", "inspect", "--format", IDENTITY_TEMPLATE, container
    ])
    fields = raw.split("|")
    if (len(fields) != 3 or IMAGE_RE.fullmatch(fields[0]) is None
            or not fields[1] or fields[2] != "true"):
        raise ValueError("running AssistX container identity not established")
    return raw


def compose_defaults_pinned(root: Path = ROOT) -> bool:
    compose = (root / "docker-compose.yml")
    if not compose.is_file() or compose.is_symlink() or compose.stat().st_size > 100000:
        return False
    data = compose.read_text()
    for key in SETTINGS:
        expected = f"{key}=" + "$" + "{" + f"{key}:-http://llm-router:8088" + "}"
        if data.count(expected) != 1:
            return False
    # This verifies ONLY the API's router URLs and its approved bridge.
    # Hermes must remain isolated; attaching it to git_default violates
    # release-verify-compose and is deliberately not accepted here.
    return "      - git_default" in data


def evaluate(before: str, after: str, result: dict,
             *, compose_defaults_correct: bool) -> dict:
    if before != after:
        raise ValueError("container restarted or image changed during preflight")
    if not isinstance(result, dict) or set(result.get("settings", {})) != set(SETTINGS):
        raise ValueError("both explicit runtime URL settings are required")
    settings = result["settings"]
    hermes = result.get("hermes")
    if not isinstance(hermes, dict):
        raise ValueError("Hermes runtime proof missing")
    if set(hermes) != {
        "configured", "canonical_internal_router_v1_url", "embedded_userinfo",
        "router_alias_dns_resolves", "router_alias_tcp_reachable",
        "router_alias_health_http_status", "credentials_or_headers_sent",
        "response_bodies_read", "production_mutation", "identity_stable",
    }:
        raise ValueError("unexpected Hermes runtime proof fields")
    if (any(type(hermes.get(key)) is not bool for key in (
        "configured", "canonical_internal_router_v1_url", "embedded_userinfo",
        "router_alias_dns_resolves", "router_alias_tcp_reachable",
        "credentials_or_headers_sent", "response_bodies_read",
        "production_mutation", "identity_stable",
    )) or hermes.get("router_alias_health_http_status") not in (None, 200)
        or hermes["credentials_or_headers_sent"]
        or hermes["response_bodies_read"] or hermes["production_mutation"]):
        raise ValueError("Hermes runtime proof malformed or mutated")
    if any(not isinstance(v, dict) or
           not all(type(v.get(k)) is bool for k in (
               "configured", "canonical_internal_router_url", "embedded_userinfo"
           )) for v in settings.values()):
        raise ValueError("runtime settings receipt malformed")
    if (type(result.get("router_alias_dns_resolves")) is not bool or
        type(result.get("router_alias_tcp_reachable")) is not bool or
        result.get("router_alias_health_http_status") not in (None, 200) or
        result.get("credentials_or_headers_sent") is not False or
        result.get("response_bodies_read") is not False or
        result.get("production_mutation") is not False):
        raise ValueError("untrusted runtime or non-read-only response")
    checks = {
        "compose_defaults_use_router_alias":compose_defaults_correct,
        "assistx_container_identity_stable":before == after,
        "both_runtime_urls_configured":all(x["configured"] for x in settings.values()),
        "both_runtime_urls_use_router_alias":all(
            x["canonical_internal_router_url"] for x in settings.values()
        ),
        "no_embedded_url_credentials":all(
            not x["embedded_userinfo"] for x in settings.values()
        ),
        "internal_router_alias_dns_resolves":result["router_alias_dns_resolves"],
        "internal_router_alias_tcp_reachable":result["router_alias_tcp_reachable"],
        "internal_router_alias_get_health_200":
            result["router_alias_health_http_status"] == 200,
        "hermes_adapter_identity_stable": hermes["identity_stable"],
        "hermes_adapter_router_v1_url_configured": hermes["configured"],
        "hermes_adapter_uses_internal_router_v1_url":
            hermes["canonical_internal_router_v1_url"],
        "hermes_adapter_no_url_credentials": not hermes["embedded_userinfo"],
        "hermes_adapter_router_dns_resolves": hermes["router_alias_dns_resolves"],
        "hermes_adapter_router_tcp_reachable": hermes["router_alias_tcp_reachable"],
        "hermes_adapter_router_health_200":
            hermes["router_alias_health_http_status"] == 200,
        # A functioning Hermes alias alone is NOT safe: Hermes currently
        # runs in a distinct Docker trust zone. The release-verify-compose
        # gate rejects joining the router's bridge. Only a separately
        # reviewed scoped /v1 relay preserving isolation can close this.
        "hermes_scoped_private_relay_reviewed": False,
    }
    return {
        "schema": SCHEMA,
        "read_only": True,
        "container_mutated": False,
        "credential_values_accessed": False,
        "admin_requests_sent": False,
        "response_bodies_read": False,
        "settings": settings,
        "checks": checks,
        "gate": "PASS_ALIAS_PATH_PREFLIGHT_ONLY" if all(checks.values()) else "HOLD",
        "production_ready": False,
        "container_recreation_authorized": False,
        "host_port_isolation_approved": False,
    }


def audit(*, read=docker_read, root: Path = ROOT) -> dict:
    before = container_identity(read)
    hermes_before = container_identity(read, container=HERMES_CONTAINER)
    raw = read(["docker", "exec", CONTAINER, "python", "-B", "-c", RUNTIME_PROBE])
    result = json.loads(raw)
    hermes_raw = read([
        "docker", "exec", HERMES_CONTAINER, "python", "-B", "-c", HERMES_PROBE
    ])
    hermes_result = json.loads(hermes_raw)
    hermes_after = container_identity(read, container=HERMES_CONTAINER)
    result["hermes"] = {
        **hermes_result, "identity_stable": hermes_before == hermes_after
    }
    after = container_identity(read)
    return evaluate(before, after, result,
                    compose_defaults_correct=compose_defaults_pinned(root))


def write_private(path: Path, result: dict) -> None:
    if (path.parent != Path("/tmp")
            or not path.name.startswith("assistx-router-alias-")
            or path.suffix != ".json" or path.exists() or path.is_symlink()):
        raise ValueError("receipt must be a new /tmp/assistx-router-alias-*.json")
    fd = os.open(path, os.O_EXCL | os.O_CREAT | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("receipt must be a regular file")
        f.write(json.dumps(result, sort_keys=True, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-read-only", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.explicit_read_only:
        parser.error("explicit read-only operator choice required")
    try:
        result = audit()
        if args.out:
            write_private(args.out, result)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "gate": "HOLD",
                          "error_type": type(exc).__name__,
                          "production_ready": False}, sort_keys=True))
        return 2
    print(json.dumps({"schema": SCHEMA, "gate": result["gate"],
                      "checks": result["checks"], "production_ready": False},
                     sort_keys=True))
    return 0 if result["gate"] == "PASS_ALIAS_PATH_PREFLIGHT_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
