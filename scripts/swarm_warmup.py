#!/usr/bin/env python3
"""
swarm_warmup.py — idempotent live reconciliation for the AssistX swarm registry.

Run this (or have it run on session open) to make a freshly-opened orchestrator
session see ground truth: it re-probes every registered LM Studio model-endpoint
against its live /v1/models and refreshes the registry status via the AssistX API.

It does NOT restart anything. It only:
  * POSTs /api/swarm/model-endpoints/{id}/probe  (the built-in prober — writes
    status=online/offline + last_probed_at into Neo4j)
  * reports the resulting live/fleet picture

Requires: ASSISTX_API_URL (default http://localhost:8000) and basic-auth creds
via env (ASSISTX_USER / ASSISTX_PASS) or falls back to auto-assist/.env.

Usage:
  python3 scripts/swarm_warmup.py
  ASSISTX_USER=admin ASSISTX_PASS=xxx python3 scripts/swarm_warmup.py
"""
from __future__ import annotations
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime

AUTO_ASSIST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_creds_from_envfile() -> tuple[str, str]:
    """Read BASIC_AUTH_USER/PASS from auto-assist/.env if env not set."""
    u = os.getenv("ASSISTX_USER")
    p = os.getenv("ASSISTX_PASS")
    if u and p:
        return u, p
    env_path = os.path.join(AUTO_ASSIST, ".env")
    try:
        txt = open(env_path).read()
        import re
        def g(k):
            m = re.search(r"^%s=(.*)$" % re.escape(k), txt, re.M)
            return m.group(1).strip() if m else ""
        u = os.getenv("ASSISTX_USER") or g("BASIC_AUTH_USER") or g("ASSISTX_USER") or "admin"
        p = os.getenv("ASSISTX_PASS") or g("BASIC_AUTH_PASS") or g("ASSISTX_PASS") or "change-me"
        return u, p
    except Exception:
        return "admin", "change-me"


def http(method: str, url: str, auth: tuple[str, str], data=None, timeout=10):
    req = urllib.request.Request(url, method=method, data=(json.dumps(data).encode() if data else None))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        import base64
        tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    return urllib.request.urlopen(req, timeout=timeout)


def main() -> int:
    base = os.getenv("ASSISTX_API_URL", "http://localhost:8000").rstrip("/")
    auth = load_creds_from_envfile()
    print(f"[swarm_warmup] AssistX @ {base}")

    # 1) list registered endpoints
    try:
        with http("GET", f"{base}/api/swarm/model-endpoints", auth) as r:
            eps = json.loads(r.read())
    except Exception as e:
        print(f"[swarm_warmup] FAILED to list endpoints: {e}")
        return 1
    items = eps if isinstance(eps, list) else eps.get("items", [])
    print(f"[swarm_warmup] {len(items)} endpoints registered — probing each...")

    # 2) re-probe every endpoint (self-heals stale status)
    live, dead = [], []
    for e in items:
        eid = e.get("model_endpoint_id") or e.get("id")
        try:
            with http("POST", f"{base}/api/swarm/model-endpoints/{eid}/probe", auth, timeout=8) as r:
                code = r.status
        except Exception as ex:
            code = f"ERR:{ex}"
        status = "ok" if code in (200, 201, 202, 204) else f"fail({code})"
        # re-read status after probe
        try:
            with http("GET", f"{base}/api/swarm/model-endpoints", auth) as r2:
                pass  # bulk refresh below
        except Exception:
            pass
        if status == "ok":
            live.append(eid)
        else:
            dead.append((eid, status))
        print(f"  probe {eid:<40} -> {status}")

    # 3) re-read registry to report reconciled status
    try:
        with http("GET", f"{base}/api/swarm/model-endpoints", auth) as r:
            eps2 = json.loads(r.read())
        items2 = eps2 if isinstance(eps2, list) else eps2.get("items", [])
        by_status = {}
        for e in items2:
            by_status[e.get("status")] = by_status.get(e.get("status"), 0) + 1
        print(f"\n[swarm_warmup] reconciled endpoint status: {by_status}")
    except Exception as e:
        print(f"[swarm_warmup] (could not re-read status: {e})")

    # 4) distinct live workers
    ips = {}
    for e in items:
        b = e.get("base_url") or e.get("base")
        ips.setdefault(b, []).append(e.get("model_endpoint_id") or e.get("id"))
    print(f"[swarm_warmup] {len(ips)} distinct LM Studio workers live:")
    for ip, ids in sorted(ips.items()):
        print(f"  {ip:<32} {len(ids)} endpoint(s)")

    print(f"\n[swarm_warmup] done. live_probes={len(live)} dead={len(dead)}")
    if dead:
        print("[swarm_warmup] WARNING — these did not probe cleanly (may be down or auth issue):")
        for eid, st in dead:
            print(f"   {eid}: {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
