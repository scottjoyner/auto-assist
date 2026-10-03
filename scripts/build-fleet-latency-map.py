#!/usr/bin/env python3
"""Build an observation-only AssistX fleet latency map."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from assistx.fleet_latency_collector import build_latency_map


def _load(path: Path | None, *, default: object) -> object:
    if path is None:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tailnet-candidates", type=Path, required=True)
    parser.add_argument("--origin-node-id", default="x1-370")
    parser.add_argument("--endpoint-evidence", type=Path)
    parser.add_argument("--pressure", type=Path)
    parser.add_argument("--stale-after-seconds", type=int, default=300)
    parser.add_argument("--ping-count", type=int, default=4)
    parser.add_argument("--ping-timeout-seconds", type=int, default=2)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/fleet-latency-map.json"),
    )
    args = parser.parse_args()

    tailnet = _load(args.tailnet_candidates, default={})
    endpoints = _load(args.endpoint_evidence, default=[])
    pressure = _load(args.pressure, default=[])
    if not isinstance(tailnet, dict):
        raise SystemExit("tailnet candidate input must be an object")
    if not isinstance(endpoints, list):
        raise SystemExit("endpoint evidence input must be an array")
    if not isinstance(pressure, list):
        raise SystemExit("pressure input must be an array")

    document, failures = build_latency_map(
        origin_node_id=args.origin_node_id,
        tailnet_candidates=tailnet,
        endpoint_evidence=endpoints,
        pressures=pressure,
        stale_after_seconds=args.stale_after_seconds,
        count=args.ping_count,
        timeout_seconds=args.ping_timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        document.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    if failures:
        print(json.dumps({"probe_failures": failures}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
