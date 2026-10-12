#!/usr/bin/env python3
"""Offline fleet endpoint gate: local lease telemetry and mobile source binding."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assistx.fleet_endpoint_harness import _read_json
from assistx.fleet_endpoint_shadow_gate import bind_mobile_projection, shadow_quota_assessment
from assistx.fleet_sso_ingest import untrusted_replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--lease-snapshot", type=Path)
    parser.add_argument("--public-mobile-projection", type=Path)
    parser.add_argument("--private-tailnet-witness", type=Path)
    parser.add_argument("--sso-whoami-json", type=Path)
    parser.add_argument("--sso-origin")
    parser.add_argument("--max-age-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if (bool(args.public_mobile_projection) != bool(args.private_tailnet_witness)
            or bool(args.sso_whoami_json) != bool(args.sso_origin)
            or (args.sso_whoami_json and not args.private_tailnet_witness)):
        print(json.dumps({"status": "REJECT", "reason": "incomplete_mobile_source_pair"}))
        return 2
    try:
        current = time.time()
        report = _read_json(args.report)
        lease = _read_json(args.lease_snapshot) if args.lease_snapshot else None
        verdict = shadow_quota_assessment(
            report, lease, now=current, max_age_seconds=args.max_age_seconds,
        )
        if args.public_mobile_projection:
            p = args.private_tailnet_witness
            if p.is_symlink() or not p.is_file() or p.stat().st_size > 2_000_000:
                raise ValueError("invalid private witness file")
            public = _read_json(args.public_mobile_projection)
            private = p.read_bytes()
            verdict["mobile_source_binding"] = bind_mobile_projection(
                public, private, now=current, max_age_seconds=args.max_age_seconds,
            )
            if args.sso_whoami_json:
                verdict["sso_contract_replay"] = untrusted_replay(
                    args.sso_origin, _read_json(args.sso_whoami_json),
                    public, private, now=current,
                )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "REJECT", "reason": type(exc).__name__}))
        return 2
    print(json.dumps(verdict, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
