#!/usr/bin/env python3
"""Read-only audit of a proposed PRIVATE Tailscale Serve fleet-trace endpoint."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys

def audit(config: dict, *, dns_name: str, port: int, target_port: int) -> dict:
    key = f"{dns_name.rstrip('.')}:{port}"
    web = config.get("Web") or {}
    funnel = config.get("AllowFunnel") or {}
    handlers = (web.get(key) or {}).get("Handlers") or {}
    intended = f"http://127.0.0.1:{target_port}"
    matching = handlers.get("/", {}).get("Proxy") == intended
    occupied = bool(handlers) and not matching
    leak = any(
        public and any(
            str(item.get("Proxy", "")).startswith(intended)
            for item in ((web.get(host) or {}).get("Handlers") or {}).values()
        )
        for host, public in funnel.items()
    )
    private = not bool(funnel.get(key))
    return {
        "port": port,
        "expected_root": intended,
        "existing_expected_mapping": matching,
        "port_occupied_by_other_service": occupied,
        "funnel_on_target": not private,
        "existing_public_trace_mapping": leak,
        "safe_to_configure_private_serve": private and not occupied and not leak,
    }

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8447)
    parser.add_argument("--local-port", type=int, default=8794)
    parser.add_argument("--dns-name")
    args = parser.parse_args(argv)
    if not (1024 <= args.port <= 65535 and 1024 <= args.local_port <= 65535):
        parser.error("ports must be >=1024 and <=65535")
    try:
        status = json.loads(subprocess.check_output(
            ["tailscale", "serve", "status", "--json"], timeout=10
        ))
        own = json.loads(subprocess.check_output(
            ["tailscale", "status", "--json"], timeout=10
        ))
        name = args.dns_name or ((own.get("Self") or {}).get("DNSName") or "").rstrip(".")
        if not name:
            raise ValueError("missing Tailscale self DNSName")
        result = audit(status, dns_name=name, port=args.port,
                       target_port=args.local_port)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result["safe_to_configure_private_serve"] else 2
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"read_only_audit_failed: {type(exc).__name__}", file=sys.stderr)
        return 3

if __name__ == "__main__":
    raise SystemExit(main())
