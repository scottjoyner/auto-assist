#!/usr/bin/env python3
"""Read-only fleet endpoint harness projection. Does not call endpoints."""
from __future__ import annotations

import argparse
import json
import os
import pwd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistx.fleet_endpoint_harness import _read_json, build_report
from assistx.fleet_tailnet_witness import adapt_tailnet_witness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--presence", type=Path)
    parser.add_argument("--local-observations", type=Path)
    parser.add_argument("--tailnet-witness", type=Path)
    parser.add_argument("--state-root", type=Path,
                        default=Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/auto-assist/subagents")
    parser.add_argument("--max-age-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        if args.tailnet_witness and args.local_observations:
            raise ValueError("use either witness or local observations")
        source = _read_json(args.inventory)
        local = _read_json(args.local_observations) if args.local_observations else None
        adapter_summary = None
        if args.tailnet_witness:
            if source.get("schema") != "fleet-endpoint-inventory/v1" or not isinstance(source.get("endpoints"), list):
                raise ValueError("invalid base inventory")
            converted, local, adapter_summary = adapt_tailnet_witness(_read_json(args.tailnet_witness))
            source["endpoints"] = source["endpoints"] + converted["endpoints"]
        report = build_report(
            source,
            presence=_read_json(args.presence) if args.presence else None,
            local_observations=local,
            state_root=args.state_root,
            freshness_seconds=args.max_age_seconds,
        )
        if adapter_summary is not None:
            report["tailnet_adapter"] = adapter_summary
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # Never dump source JSON or credential-bearing exception payloads.
        print(json.dumps({"status": "REJECT", "reason": type(exc).__name__}))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
