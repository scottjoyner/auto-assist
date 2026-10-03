#!/usr/bin/env python3
"""
swarm_warmup.py — idempotent live reconciliation for the AssistX swarm registry.

Run this (or have it run on session open) to make a freshly-opened orchestrator
session see ground truth: it re-probes every registered LM Studio model-endpoint
against its live /v1/models and refreshes the registry status via the AssistX API.

It does NOT restart anything. By default it is READ-ONLY: it probes, self-heals
stale online/offline status, and reports the live/fleet picture. It also always
captures a cheap snapshot of the current endpoint registry to SSD so the state
can be restored later if ever needed (see --restore).

With --prune (and interactive confirm), it removes endpoints that came back
OFFLINE during this run — but only after printing exactly what will be removed,
and only *after* writing a timestamped snapshot of the registry to SSD first.
Pruning is opt-in; nothing is ever deleted without an explicit --prune flag and
a positive confirmation (or --yes).

Requires: ASSISTX_API_URL (default http://localhost:8000) and basic-auth creds
via env (ASSISTX_USER / ASSISTX_PASS) or falls back to auto-assist/.env.

Usage:
  python3 scripts/swarm_warmup.py                 # read-only probe + snapshot
  python3 scripts/swarm_warmup.py --prune          # prompt, then delete dead ones
  python3 scripts/swarm_warmup.py --prune --yes    # prune without the prompt
  python3 scripts/swarm_warmup.py --restore latest  # re-register from a snapshot
"""
from __future__ import annotations
import argparse
import datetime
import os
import sys
import json
import urllib.request
import urllib.error

AUTO_ASSIST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where to keep registry snapshots for restore. SSD mirror is the shared workspace;
# if it isn't mounted we fall back to a local state/ dir so the tool still works.
FLEET_STATE_DIR = os.path.join(
    "/media/scott/SSD_4TB/hermes-home", "FLEET-STATE"
)


def _resolve_state_dir() -> str:
    try:
        os.makedirs(FLEET_STATE_DIR, exist_ok=True)
        # verify writability before relying on it
        probe = os.path.join(FLEET_STATE_DIR, ".write_test")
        open(probe, "w").close()
        os.remove(probe)
        return FLEET_STATE_DIR
    except Exception:
        local = os.path.join(AUTO_ASSIST, "state")
        try:
            os.makedirs(local, exist_ok=True)
            return local
        except Exception:
            return os.getcwd()


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

        u = (
            os.getenv("ASSISTX_USER") or g("BASIC_AUTH_USER")
            or g("ASSISTX_USER") or "admin"
        )
        p = (
            os.getenv("ASSISTX_PASS") or g("BASIC_AUTH_PASS")
            or g("ASSISTX_PASS") or "change-me"
        )
        return u, p
    except Exception:
        return "admin", "change-me"


def http(method: str, url: str, auth: tuple[str, str], data=None, timeout=10):
    req = urllib.request.Request(url, method=method,
                                 data=(json.dumps(data).encode() if data else None))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        import base64
        tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    return urllib.request.urlopen(req, timeout=timeout)


def list_endpoints(base, auth):
    with http("GET", f"{base}/api/swarm/model-endpoints", auth) as r:
        eps = json.loads(r.read())
    return eps if isinstance(eps, list) else eps.get("items", [])


def save_registry_snapshot(items) -> str:
    """Cheap pre-prune capture. Always runs so --restore is always ready.

    Writes both a timestamped copy (retention) and latest.json (convenience).
    Returns the path of the timestamped snapshot.
    """
    out_dir = _resolve_state_dir()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamp = os.path.join(out_dir, f"model-endpoints-snapshot.{ts}.json")
    payload = {
        "created_at_utc": ts,
        "count": len(items),
        "source": "swarm_warmup",
        "items": items,
    }
    try:
        with open(stamp, "w") as fh:
            json.dump(payload, fh, indent=2)
    except Exception as e:
        print(f"[snapshot] failed to write {stamp}: {e}")
        return ""
    # convenience copy
    latest = os.path.join(out_dir, "model-endpoints.latest.json")
    try:
        with open(latest, "w") as fh:
            json.dump(payload, fh, indent=2)
    except Exception:
        pass
    return stamp


def restore_from_snapshot(label="latest"):
    """Re-register endpoints from a prior snapshot (idempotent upserts)."""
    base = os.getenv("ASSISTX_API_URL", "http://localhost:8000").rstrip("/")
    auth = load_creds_from_envfile()
    out_dir = _resolve_state_dir()
    import glob
    if label == "latest":
        candidates = [os.path.join(out_dir, "model-endpoints.latest.json")]
    else:
        candidates = [os.path.join(out_dir, label)] if os.path.exists(
            os.path.join(out_dir, label)) else []
        if not candidates:
            matches = sorted(glob.glob(os.path.join(out_dir, "model-endpoints-*.json")))
            hits = [m for m in matches if label in m] or matches
            candidates = hits
    path = candidates[-1] if candidates else ""
    if not path or not os.path.exists(path):
        print(f"[restore] no snapshot found for '{label}' at {candidates}")
        return 2
    data = json.load(open(path))
    items = data.get("items", [])
    print(f"[restore] re-registering {len(items)} endpoints from {os.path.basename(path)}")
    ok, fail = 0, 0
    for it in items:
        reg = {k: it.get(k) for k in (
            "model_endpoint_id", "node_id", "base_url", "provider",
            "status", "auth_type", "network_preference", "purpose",
        )}
        if not (reg.get("model_endpoint_id") and reg.get("node_id") and reg.get("base_url")):
            print(f"  skip {it.get('model_endpoint_id','?')} (missing required fields)")
            continue
        try:
            with http("POST", f"{base}/api/swarm/model-endpoints/register", auth, reg, timeout=8) as r:
                if r.status in (200, 201, 202, 204):
                    ok += 1
                else:
                    fail += 1
        except Exception as e:
            print(f"  err {reg.get('model_endpoint_id')}: {e}")
            fail += 1
    print(f"[restore] done. restored={ok} failed={fail}")
    return 0 if fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="AssistX swarm endpoint reconciliation")
    ap.add_argument("--prune", action="store_true",
                    help="delete endpoints that came back offline (prompted unless --yes)")
    ap.add_argument("--yes", action="store_true",
                    help="do not prompt before pruning")
    ap.add_argument("--restore", metavar="LABEL", default=None,
                    help="re-register from a snapshot ('latest' or timestamp name)")
    args = ap.parse_args()

    if args.restore is not None:
        return restore_from_snapshot(args.restore)

    base = os.getenv("ASSISTX_API_URL", "http://localhost:8000").rstrip("/")
    auth = load_creds_from_envfile()
    print(f"[swarm_warmup] AssistX @ {base}")

    # 1) list registered endpoints (this is also the pre-prune state we snapshot)
    try:
        items = list_endpoints(base, auth)
    except Exception as e:
        print(f"[swarm_warmup] FAILED to list endpoints: {e}")
        return 1
    print(f"[swarm_warmup] {len(items)} endpoints registered — probing each...")

    # Always capture state before doing anything (cheap, one JSON dump) so restore
    # is always ready. This runs even in read-only mode.
    snap_path = save_registry_snapshot(items)
    if snap_path:
        print(f"[swarm_warmup] snapshot captured -> {snap_path}")

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
        if status == "ok":
            live.append(eid)
        else:
            dead.append((eid, e.get("base_url"), status))
        print(f"  probe {eid:<40} -> {status}")

    # 3) re-read registry to report reconciled status
    try:
        items2 = list_endpoints(base, auth)
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

    # Dead endpoints. In read-only mode this is a warning; --prune deletes them.
    print(f"\n[swarm_warmup] done. live_probes={len(live)} dead={len(dead)}")
    if dead:
        print("[swarm_warmup] WARNING — these did not probe cleanly (may be down or auth issue):")
        for eid, url, st in dead:
            print(f"   {eid}: {st}  ({url})")
        if args.prune:
            what = ", ".join(eid for eid, _, _ in dead)
            print(f"[swarm_warmup] --prune selected. Would delete {len(dead)} dead endpoint(s): {what}")
            if not args.yes:
                ans = input("[swarm_warmup] Delete these dead endpoints now? [y/N] ").strip().lower()
                if ans not in ("y", "yes"):
                    print("[swarm_warmup] aborting — nothing deleted (snapshot retained for restore)")
                    return 0

            # delete offline endpoints via the existing DELETE endpoint
            pruned, failed = [], []
            for eid, _, _ in dead:
                try:
                    with http("DELETE", f"{base}/api/swarm/model-endpoints/{eid}", auth, timeout=8) as r:
                        code = r.status
                    if code in (200, 201, 202, 204):
                        pruned.append(eid)
                    else:
                        failed.append((eid, f"HTTP {code}"))
                except Exception as ex:
                    failed.append((eid, str(ex)))
            print(f"[swarm_warmup] pruned={len(pruned)} failed={len(failed)}")
            for eid in pruned:
                print(f"  deleted {eid}")
            for eid, why in failed:
                print(f"  prune-failed {eid}: {why}")
        else:
            print("[swarm_warmup] (read-only; re-run with --prune to remove these)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
