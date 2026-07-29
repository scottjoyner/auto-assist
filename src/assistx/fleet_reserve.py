#!/usr/bin/env python3
"""
fleet_reserve.py — set/clear a fleet endpoint reservation lock.

A reserved endpoint will NOT be selected by the AssistX hermes-adapter's
fleet.select_any() for swarm/self tasks, so a node dedicated to an interactive
Hermes session (e.g. ornith-1.0-35b on x1-370:1234) is never clobbered by
background fleet work.

Usage:
  fleet_reserve.py reserve <node> [--port 1234] [--by NAME] [--minutes N] [--purpose TEXT]
  fleet_reserve.py release <node> [--port 1234]
  fleet_reserve.py list

<node> is the tailnet hostname or IP the fleet uses (e.g. x1-370, 100.64.43.123).
The lock is keyed by "<node>:<port>" to match fleet.select_any's node strings.
Reservations auto-expire after --minutes (default 240) so a crashed session
releases the node without manual cleanup.
"""
import argparse
import json
import os
import sys
import time

# Live path inside the assistx-hermes-adapter container (mounted from host ./src)
RESERVE_PATH = os.path.join(os.path.dirname(__file__), "fleet_reservations.json")
DEFAULT_PORT = 1234
DEFAULT_TTL_MIN = 240


def load():
    if not os.path.exists(RESERVE_PATH):
        return {}
    try:
        with open(RESERVE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save(data):
    os.makedirs(os.path.dirname(RESERVE_PATH), exist_ok=True)
    with open(RESERVE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def key(node, port):
    return f"{node}:{port}"


def cmd_reserve(args):
    data = load()
    k = key(args.node, args.port)
    data[k] = {
        "by": args.by or f"hermes-session-{int(time.time())}",
        "until": time.time() + args.minutes * 60,
        "purpose": args.purpose or "interactive hermes session",
        "set_at": time.time(),
    }
    save(data)
    print(f"RESERVED {k} by={data[k]['by']} until={time.ctime(data[k]['until'])} ({args.minutes}m) purpose={data[k]['purpose']}")


def cmd_release(args):
    data = load()
    k = key(args.node, args.port)
    if k in data:
        del data[k]
        save(data)
        print(f"RELEASED {k}")
    else:
        print(f"no reservation for {k}")


def cmd_list(args):
    data = load()
    now = time.time()
    if not data:
        print("no active reservations")
        return
    for k, v in data.items():
        state = "EXPIRED" if v["until"] < now else "ACTIVE"
        print(f"{k:30s} {state:8s} by={v['by']} until={time.ctime(v['until'])} purpose={v['purpose']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reserve")
    r.add_argument("node")
    r.add_argument("--port", type=int, default=DEFAULT_PORT)
    r.add_argument("--by", default=None)
    r.add_argument("--minutes", type=int, default=DEFAULT_TTL_MIN)
    r.add_argument("--purpose", default=None)
    r.set_defaults(func=cmd_reserve)
    l = sub.add_parser("release")
    l.add_argument("node")
    l.add_argument("--port", type=int, default=DEFAULT_PORT)
    l.set_defaults(func=cmd_release)
    ls = sub.add_parser("list")
    ls.set_defaults(func=cmd_list)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
