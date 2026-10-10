#!/usr/bin/env python3
"""Send one metadata-only event over private Serve. Never use Funnel."""
import argparse
import datetime
import json
import secrets
import urllib.request
import uuid

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--node-id", required=True)
    p.add_argument("--producer", required=True,
                   choices=["assistx", "opencode", "hermes", "kipnerter", "worker"])
    p.add_argument("--operation", default="heartbeat",
                   choices=["session-start", "session-end", "model", "tool", "delegate",
                            "approval", "retry", "heartbeat", "context", "error"])
    p.add_argument("--traceparent", default=None)
    args = p.parse_args()
    from assistx.fleet_trace_ingress import TRACEPARENT
    if args.traceparent:
        match = TRACEPARENT.fullmatch(args.traceparent)
        if not match:
            p.error("invalid traceparent")
        trace_id = match[1]
    else:
        trace_id = uuid.uuid4().hex
    span = secrets.token_hex(8)
    row = {
        "schema": "assistx.fleet.trace.v1",
        "event_id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "span_id": span,
        "session_id": "session-" + uuid.uuid4().hex,
        "node_id": args.node_id,
        "producer": args.producer,
        "operation": args.operation,
        "status": "ok",
        "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sequence": 0,
    }
    req = urllib.request.Request(
        args.endpoint.rstrip("/") + "/v1/events",
        data=json.dumps(row).encode(),
        method="POST",
        headers={"Content-Type": "application/json",
                 "traceparent": f"00-{trace_id}-{span}-01"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        receipt = json.load(response)
        print(json.dumps({
            "http": response.status,
            "event_id": receipt.get("event_id"),
            "sha256": receipt.get("sha256"),
            "accepted": receipt.get("accepted"),
            "source_attested": receipt.get("source_attested"),
        }, sort_keys=True))

if __name__ == "__main__":
    main()
