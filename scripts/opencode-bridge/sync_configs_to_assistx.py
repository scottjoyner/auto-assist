#!/usr/bin/env python3
"""
sync_configs_to_assistx.py — DYNAMIC bridge (Hybrid A, evolved).

Reads the Hermes config + opencode config from every reachable node and pushes
the resulting model fleet into AssistX as swarm ModelEndpoints, then refreshes
auto-router's live-model projection. Re-run on a cron (or file-watch) so the
AssistX/auto-router universe always mirrors the real local-fleet configs — not
a one-shot snapshot.

Sources consumed (single source of truth = the config files themselves):
  - opencode config:   ~/.config/opencode/opencode.jsonc (symlink target)
                        + x1-370 mirror at $X1_MIRROR/home_scott_.config/opencode/opencode.jsonc
  - hermes config:     ~/.hermes/config.yaml  (fleet block: tailnet/known_hosts/model_routing)

Behavior:
  - Each opencode `provider.*.options.baseURL` -> one ModelEndpoint
    (model_endpoint_id = <node>.<provkey>, e.g. x1-370.lm_x1_370).
  - 127.0.0.1 / localhost endpoints are SKIPPED (not routable cross-node).
  - Endpoints are deduped by IP:PORT so two configs pointing at the same host
    collapse to one record (fixes the deathstar/optiplex duplicate swarm entries).
  - Idempotent upsert; probing discovers live /v1/models.

Env:
  ASSISTX_URL       default http://100.64.43.123:8000
  ASSISTX_USER      default admin
  ASSISTX_PASS      *** (required — real AssistX admin pass)
  AUTO_ROUTER_URL   default http://100.64.43.123:8088
  X1_MIRROR         default /media/scott/SSD_4TB/hermes-home/home_scott_.config
  OPENCODE_CONFIG   default ~/.config/opencode/opencode.local-fleet.json

Usage:
  ASSISTX_PASS=*** python3 sync_configs_to_assistx.py
  ASSISTX_PASS=*** python3 sync_configs_to_assistx.py --dry-run   # print plan, no network
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
import base64
from urllib.parse import urlparse

DEFAULT_OPENCODE = os.path.expanduser("~/.config/opencode/opencode.local-fleet.json")
DEFAULT_X1_MIRROR = "/media/scott/SSD_4TB/hermes-home/home_scott_.config"
HERMES_CONFIG = os.path.expanduser("~/.hermes/config.yaml")

# Canonical tailnet IP -> swarm node_id (stable; from live AssistX swarm + memory).
# Lets us label a provider by the machine it actually runs on, regardless of which
# opencode config file declared it.
IP_TO_NODE = {
    "100.64.43.123": "x1-370",
    "100.108.99.47": "xwing",
    "100.85.64.117": "scotts-macbook-air",
    "100.78.106.121": "deathstar",
    "100.81.57.77": "destroyer",
    "100.69.158.114": "optiplex",
    "100.85.72.121": "beelink-ryzen-7-mini-pc",
    "100.105.137.98": "scott-lenovo-ideapad-330s-15ikb",
    "100.83.215.83": "joyner",
}


def _host_of(base_url: str) -> str:
    """Return host:port from a baseURL, normalized for dedup."""
    if not base_url:
        return ""
    # strip scheme and /v1
    netloc = base_url.split("://", 1)[-1].split("/")[0]
    if ":" not in netloc:
        netloc += ":1234"  # lmstudio default
    return netloc


def _node_for_host(host: str) -> str:
    ip = host.split(":")[0]
    return IP_TO_NODE.get(ip, ip.replace(".", "-"))


def _post(url, payload, user, password, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ! HTTP {e.code} {url}\n    {e.read().decode()[:200]}")
        return {}
    except Exception as e:  # noqa
        print(f"  ! {type(e).__name__} {url}: {e}")
        return {}


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"  ! bad JSON {path}: {e}")
        return None


def load_yaml(path):
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def discover_opencode_endpoints(cfg, node_hint: str):
    """Yield (node_id, endpoint_id, base_url, display) from an opencode config.

    node_id is derived from the provider's actual tailnet IP (via IP_TO_NODE),
    NOT from which config file declared it — so xwing's `lm_x1_370` provider is
    correctly labeled node `x1-370`.
    """
    out = []
    if not cfg:
        return out
    providers = cfg.get("provider", {})
    for pkey, pval in providers.items():
        base = pval.get("options", {}).get("baseURL", "")
        host = _host_of(base)
        if not host:
            continue
        if host.startswith("127.0.0.1") or host.startswith("localhost"):
            continue  # not routable cross-node
        node = _node_for_host(host)
        endpoint_id = f"{node}.{pkey}"
        disp = pval.get("name", f"{pkey} on {node}")
        out.append((node, endpoint_id, f"http://{host}", disp))
    return out


def run_sync(dry_run: bool = False, password: str = "",
             assistx_url: str = "http://100.64.43.123:8000",
             user: str = "admin",
             auto_router_url: str = "http://100.64.43.123:8088",
             opencode_config: str = DEFAULT_OPENCODE,
             x1_mirror: str = DEFAULT_X1_MIRROR) -> int:
    """Sync opencode + Hermes configs into AssistX (Hybrid A). Returns # endpoints."""
    # 1) Hermes fleet topology (node names <-> tailnet hosts)
    hermes = load_yaml(HERMES_CONFIG) or {}
    fleet = hermes.get("fleet", {}) or {}
    known_hosts = fleet.get("known_hosts", []) or []
    print(f"Hermes fleet: enabled={fleet.get('enabled')}, tailnet={fleet.get('tailnet')}, "
          f"known_hosts={len(known_hosts)}")

    # 2) opencode configs: xwing (local) + x1-370 (mirror, if intact)
    plan = []  # (node, endpoint_id, base_url, display)
    seen_hosts = set()

    xwing_cfg = load_json(opencode_config)
    for node, eid, base, disp in discover_opencode_endpoints(xwing_cfg, "xwing"):
        host = _host_of(base)
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        plan.append((node, eid, base, disp))

    x1_cfg_path = os.path.join(x1_mirror, "opencode", "opencode.jsonc")
    x1_cfg = load_json(x1_cfg_path)
    if x1_cfg:
        for node, eid, base, disp in discover_opencode_endpoints(x1_cfg, "x1-370"):
            host = _host_of(base)
            if host in seen_hosts:
                print(f"  (dedup) skip {eid} -> {host} (already registered)")
                continue
            seen_hosts.add(host)
            plan.append((node, eid, base, disp))
    else:
        print(f"  (warn) x1-370 opencode mirror not found or corrupt at {x1_cfg_path}")

    print(f"\nPlanned ModelEndpoints ({len(plan)}):")
    for node, eid, base, disp in plan:
        print(f"  - {eid:32} {base:32} [{node}]")

    if dry_run:
        print("\n--dry-run: no changes made.")
        return len(plan)

    if not password:
        print("ERROR: ASSISTX_PASS required.", file=sys.stderr)
        sys.exit(2)

    registered = []
    for node, eid, base, disp in plan:
        payload = {
            "model_endpoint_id": eid,
            "node_id": node,
            "base_url": base,
            "provider": "lm_studio",
            "status": "online",
            "auth_type": "none",
            "network_preference": "tailscale",
            "purpose": f"opencode lane — {disp}",
        }
        resp = _post(f"{assistx_url}/api/swarm/model-endpoints/register", payload,
                     user, password)
        if resp:
            print(f"[register] {eid} ok")
        _post(f"{assistx_url}/api/swarm/model-endpoints/{eid}/probe", {},
              user, password)
        registered.append(eid)

    # refresh auto-router live-model projection
    print(f"[router] refresh {auto_router_url}/admin/live-models/refresh")
    _post(f"{auto_router_url}/admin/live-models/refresh", {}, "", "")

    print(f"\nSynced {len(registered)} endpoints. Re-run on change to keep AssistX in sync.")
    return len(registered)


def main():
    ap = argparse.ArgumentParser(description="Dynamically sync Hermes+opencode configs into AssistX")
    ap.add_argument("--dry-run", action="store_true", help="print the endpoint plan, make no network calls")
    ap.add_argument("--assistx-url", default=os.environ.get("ASSISTX_URL", "http://100.64.43.123:8000"))
    ap.add_argument("--user", default=os.environ.get("ASSISTX_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("ASSISTX_PASS", ""))
    ap.add_argument("--auto-router-url", default=os.environ.get("AUTO_ROUTER_URL", "http://100.64.43.123:8088"))
    ap.add_argument("--opencode-config", default=os.environ.get("OPENCODE_CONFIG", DEFAULT_OPENCODE))
    ap.add_argument("--x1-mirror", default=os.environ.get("X1_MIRROR", DEFAULT_X1_MIRROR))
    args = ap.parse_args()

    run_sync(
        dry_run=args.dry_run,
        password=args.password,
        assistx_url=args.assistx_url,
        user=args.user,
        auto_router_url=args.auto_router_url,
        opencode_config=args.opencode_config,
        x1_mirror=args.x1_mirror,
    )


if __name__ == "__main__":
    main()
