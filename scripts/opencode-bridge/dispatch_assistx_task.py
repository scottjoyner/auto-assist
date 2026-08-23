#!/usr/bin/env python3
"""
dispatch_assistx_task.py — Hybrid bridge part C (dispatch).

Submits a task to AssistX as READY (written to Neo4j), so the Hermes adapter polls,
claims, and executes it. This makes opencode an entry point into the AssistX
task lifecycle (READY -> RUNNING -> DONE), with full graph traceability.

The task is written via the AssistX event envelope contract (POST /api/events),
which is the same channel Sophia voice and other feeders use. We emit a
Task(node=opencode, source=opencode_cli) envelope with a unique correlation_id.

Usage:
  python3 dispatch_assistx_task.py "Refactor opencode router integration" --agent build
  python3 dispatch_assistx_task.py "Summarize meeting transcript" --type batch --queue batch

Env:
  ASSISTX_URL  default http://100.64.43.123:8000
  ASSISTX_USER default admin
  ASSISTX_PASS *** (required)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
import base64

# Allow running from the bridge dir or as a module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_configs_to_assistx import run_sync  # noqa: E402

ASSISTX_URL = os.environ.get("ASSISTX_URL", "http://100.64.43.123:8000")


def _post(url, payload, user, password):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Dispatch an opencode task into AssistX (READY)")
    ap.add_argument("prompt", help="task description / prompt")
    ap.add_argument("--agent", default="build", help="target opencode agent (build|plan|review|...)")
    ap.add_argument("--type", default="task", help="task|batch|critical")
    ap.add_argument("--queue", default="interactive", help="interactive|batch|critical")
    ap.add_argument("--assistx-url", default=ASSISTX_URL)
    ap.add_argument("--user", default=os.environ.get("ASSISTX_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("ASSISTX_PASS", ""))
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the pre-dispatch swarm sync/refresh (uses last synced state)")
    args = ap.parse_args()

    if not args.password:
        print("ERROR: ASSISTX_PASS required.", file=sys.stderr)
        sys.exit(2)

    # On-request refresh: sync the live opencode + Hermes configs into AssistX
    # (and refresh auto-router) before dispatching, so the task enters the swarm
    # with the freshest fleet state. Skip with --no-refresh for speed.
    if not args.no_refresh:
        print("-- refreshing swarm from live configs --", file=sys.stderr)
        try:
            run_sync(
                dry_run=False,
                password=args.password,
                assistx_url=args.assistx_url,
                user=args.user,
                auto_router_url=os.environ.get("AUTO_ROUTER_URL", "http://100.64.43.123:8088"),
                opencode_config=os.environ.get("OPENCODE_CONFIG",
                    os.path.expanduser("~/.config/opencode/opencode.local-fleet.json")),
            )
        except SystemExit as e:
            if e.code not in (None, 0):
                print(f"  (warn) sync exited {e.code}; dispatching with last known state", file=sys.stderr)
        except Exception as e:  # noqa
            print(f"  (warn) sync failed: {e}; dispatching with last known state", file=sys.stderr)

    cid = str(uuid.uuid4())
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "task.created",
        "source_repo": "opencode",
        "source_service": "opencode_cli",
        "node_id": "xwing",
        "occurred_at": ts,
        "idempotency_key": cid,
        "schema_version": "1.0",
        "subject": {"kind": "Task", "id": cid, "task_id": cid, "agent": args.agent},
        "payload": {
            "prompt": args.prompt,
            "agent": args.agent,
            "task_type": args.type,
            "queue": args.queue,
            "status": "READY",
        },
        "privacy": {"pii": False, "privacy_class": "public", "retention_class": "keep"},
        "correlation_id": cid,
    }
    print(f"[dispatch] {cid} -> AssistX ({args.queue} queue, agent={args.agent})")
    resp = _post(f"{args.assistx_url}/api/events", envelope, args.user, args.password)
    print(json.dumps(resp, indent=2)[:600])
    print(f"\nTrace this task in Neo4j with correlation_id={cid}")


if __name__ == "__main__":
    main()
