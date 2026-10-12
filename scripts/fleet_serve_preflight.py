#!/usr/bin/env python3
"""Read-only Tailscale Serve route preflight; prints sanitized evidence."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assistx.fleet_serve_preflight import _self_dns, assess_serve_configuration
from assistx.fleet_serve_https_probe import probe_self
from assistx.fleet_ingress_boundary import assess_ingress_boundary


def _load_file(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("untrusted input path")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("invalid JSON data")
    return data


def _live_json(*args: str) -> dict:
    raw = subprocess.check_output(args, timeout=6)
    if len(raw) > 2_000_000:
        raise ValueError("oversized Tailscale metadata")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("invalid Tailscale metadata")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-current", action="store_true")
    parser.add_argument("--probe-self", action="store_true",
                        help="Opt-in: three read-only HTTPS GETs against this node's configured Serve listener")
    parser.add_argument("--audit-ingress", action="store_true",
                        help="Opt-in, read-only Docker AssistX API ingress inventory; emit booleans only")
    parser.add_argument("--serve-json", type=Path)
    parser.add_argument("--status-json", type=Path)
    args = parser.parse_args(argv)
    if args.probe_self and not args.read_current:
        parser.error("--probe-self requires --read-current")
    if args.audit_ingress and not args.read_current:
        parser.error("--audit-ingress requires --read-current")
    if args.read_current == bool(args.serve_json):
        parser.error("choose --read-current or the paired --serve-json/--status-json")
    if not args.read_current and not args.status_json:
        parser.error("--status-json is required with --serve-json")
    try:
        if args.read_current:
            serve = _live_json("tailscale", "serve", "status", "--json")
            status = _live_json("tailscale", "status", "--json")
        else:
            serve, status = _load_file(args.serve_json), _load_file(args.status_json)
        report = assess_serve_configuration(serve, status)
        if args.audit_ingress:
            container = _live_json("docker", "inspect", "--format",
                                   "{{json .}}", "assistx-api")
            report["ingress_boundary"] = assess_ingress_boundary(container, report)
        if args.probe_self:
            ports = report["identity_route_https_ports"]
            if len(ports) != 1:
                report["https_self_probe"] = {
                    "status": "NOT_RUN_AMBIGUOUS_OR_MISSING_IDENTITY_LISTENER",
                    "model_authorizations": 0,
                }
            else:
                report["https_self_probe"] = probe_self(_self_dns(status), ports[0])
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        print(json.dumps({"status": "REJECT", "reason": "serve_configuration_unavailable"}))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
