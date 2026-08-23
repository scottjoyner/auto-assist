#!/usr/bin/env python3
"""
router_pick.py — Hybrid bridge part B (router-aware opencode).

Resolves the best model/endpoint for a given task profile by asking auto-router,
instead of hardcoding a model id in the opencode config.

Calls POST /api/routes/request (auto-router, x1-370:8088) with a RouteRequest and
prints the chosen provider + model so an opencode command or wrapper can use it.

RouteRequest fields (verified against live openapi): only `correlation_id` is
required; optional: model, intent, context_requirements, tools, eligible_lanes,
blocked_lanes, dispatch_id, task_id, metadata.

Usage:
  python3 router_pick.py --intent build --model lm_x1_370/qwen36-35b-apex
  python3 router_pick.py --intent vision --context-requirements '{"needs_vision":true}'
  python3 router_pick.py --intent code --eligible-lanes x1-370 xwing

Env:
  AUTO_ROUTER_URL  default http://100.64.43.123:8088
  ASSISTX_PASS     used to refresh the swarm before routing (on by default)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import uuid

# Allow running from the bridge dir or as a module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_configs_to_assistx import run_sync  # noqa: E402

AUTO_ROUTER_URL = os.environ.get("AUTO_ROUTER_URL", "http://100.64.43.123:8088")


def route(req: dict) -> dict:
    url = f"{AUTO_ROUTER_URL}/api/routes/request"
    data = json.dumps(req).encode()
    r = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Ask auto-router to pick the best model for a task")
    ap.add_argument("--intent", default="build", help="intent type, e.g. build|code|review|vision|summary|long-context|voice_command")
    ap.add_argument("--intent-text", default="", help="free-text description of the task for the router")
    ap.add_argument("--priority", default="normal", help="normal|high|low")
    ap.add_argument("--model", default=None, help="requested model alias, e.g. lm_x1_370/qwen36-35b-apex")
    ap.add_argument("--needs-repo", action="store_true", help="task needs repo/local files context")
    ap.add_argument("--needs-external-web", action="store_true", help="task needs external web")
    ap.add_argument("--needs-voice-auth", action="store_true", help="task needs voice auth")
    ap.add_argument("--tools", default=None, help="JSON array of tool specs")
    ap.add_argument("--eligible-lanes", nargs="*", default=None, help="restrict to these lanes/nodes")
    ap.add_argument("--blocked-lanes", nargs="*", default=None, help="exclude these lanes/nodes")
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--dispatch-id", default=None)
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the pre-route swarm sync/refresh (faster; uses last synced state)")
    args = ap.parse_args()

    # On-request refresh: every route decision first syncs the live opencode +
    # Hermes configs into AssistX and refreshes auto-router, so the router always
    # sees the freshest fleet (loaded models, endpoints) — not a stale snapshot.
    if not args.no_refresh:
        print("-- refreshing swarm from live configs --", file=sys.stderr)
        try:
            run_sync(
                dry_run=False,
                password=os.environ.get("ASSISTX_PASS", ""),
                auto_router_url=AUTO_ROUTER_URL,
            )
        except SystemExit as e:
            if e.code not in (None, 0):
                print(f"  (warn) sync exited {e.code}; routing with last known state", file=sys.stderr)
        except Exception as e:  # noqa
            print(f"  (warn) sync failed: {e}; routing with last known state", file=sys.stderr)

    req = {
        "correlation_id": str(uuid.uuid4()),
        "intent": {
            "type": args.intent,
            "text": args.intent_text,
            "priority": args.priority,
        },
    }
    if args.model:
        req["model"] = args.model
    if any([args.needs_repo, args.needs_external_web, args.needs_voice_auth]):
        req["context_requirements"] = {
            "needs_repo": args.needs_repo,
            "needs_external_web": args.needs_external_web,
            "needs_voice_auth": args.needs_voice_auth,
            "needs_local_files": args.needs_repo,
        }
    if args.tools:
        req["tools"] = json.loads(args.tools)
    if args.eligible_lanes:
        req["eligible_lanes"] = args.eligible_lanes
    if args.blocked_lanes:
        req["blocked_lanes"] = args.blocked_lanes
    if args.task_id:
        req["task_id"] = args.task_id
    if args.dispatch_id:
        req["dispatch_id"] = args.dispatch_id

    resp = route(req)
    # Print a compact, machine-parseable result for opencode command capture
    choice = resp.get("route") or resp.get("choice") or resp
    out = {
        "provider": choice.get("provider") or choice.get("provider_id"),
        "node": choice.get("node") or choice.get("node_id"),
        "model": choice.get("model") or choice.get("model_id"),
        "rationale": choice.get("rationale"),
    }
    print(json.dumps({k: v for k, v in out.items() if v is not None}, indent=2))


if __name__ == "__main__":
    main()
