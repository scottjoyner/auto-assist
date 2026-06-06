#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistx.canary import CutoverCanaryTarget, SignedIngestSample, run_cutover_canary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AssistX cutover canary against the selected production worker.")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--worker-target", default=os.getenv("PRODUCTION_WORKER_TARGET", "Hermes Agent"))
    parser.add_argument("--expected-disposition", default=os.getenv("EXPECTED_DISPOSITION", "COMPLETED"))
    parser.add_argument("--sample-file", required=True, help="JSON file containing payload, signature_header, and signature")
    parser.add_argument("--timeout-s", type=float, default=float(os.getenv("CUTOVER_CANARY_TIMEOUT_S", "300")))
    parser.add_argument("--poll-interval-s", type=float, default=float(os.getenv("CUTOVER_CANARY_POLL_INTERVAL_S", "5")))
    args = parser.parse_args()

    sample_data = json.loads(Path(args.sample_file).read_text(encoding="utf-8"))
    sample = SignedIngestSample(
        endpoint=sample_data["endpoint"],
        payload=sample_data["payload"],
        signature_header=sample_data.get("signature_header", "X-Voice-Signature"),
        signature=sample_data["signature"],
        auth_user=sample_data.get("auth_user"),
        auth_pass=sample_data.get("auth_pass"),
    )
    result = run_cutover_canary(
        base_url=args.base_url,
        target=CutoverCanaryTarget(
            worker_target=args.worker_target,
            expected_disposition=args.expected_disposition,
        ),
        signed_enrollment_sample=sample,
        timeout_s=args.timeout_s,
        poll_interval_s=args.poll_interval_s,
    )
    print(json.dumps({
        "ingest_response": result.ingest_response,
        "dispatch_response": result.dispatch_response,
        "terminal_dispatch": result.terminal_dispatch,
        "elapsed_s": round(result.elapsed_s, 3),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
