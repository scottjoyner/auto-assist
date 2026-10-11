#!/usr/bin/env python3
"""Offline correlation of two UNSIGNED host observations; NEVER an attestation.

Join the approved GGUF/PID/boot witness and LM Studio gateway-to-worker socket
lineage only when both were independently collected recently on the same host.
Neither source can authenticate the other or mint execution/owner authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PHYSICAL_KEYS = {
    "schema", "source_kind", "observed_at_ms", "pid", "boot_id",
    "process_start_ticks", "model_artifact_fingerprint", "model_bytes",
    "continuity_checked", "signature_verified", "model_loaded_attested",
    "owner_bound", "can_approve", "can_dispatch",
}
LINEAGE_KEYS = {
    "schema", "source_kind", "observed_at_ms", "boot_id", "gateway_pid",
    "gateway_process_start_ticks", "gateway_listener_port", "worker_pid",
    "worker_process_start_ticks", "worker_listener_port",
    "worker_parent_pid", "listener_ownership_observed",
    "direct_worker_health_ok", "physical_model_digest_checked_by_this_tool",
    "model_loaded_inference_attested", "gateway_forwarding_attested",
    "source_signature_verified", "operator_approval_present",
    "owner_bound_request_attested", "can_approve", "can_dispatch",
}
CLOCK_SKEW_MS = 5_000
SOURCE_MAX_AGE_MS = 300_000
CROSS_OBSERVATION_MAX_GAP_MS = 90_000


def typed_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0 or value >= 2**63:
        raise ValueError("invalid " + label)
    return value


def envelope(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(label + " has unknown or missing fields")
    return value


def correlate(
    *, physical: object, lineage: object, expected_sha256: str,
    expected_bytes: int, expected_gateway_port: int,
    expected_worker_port: int, now_ms: int,
) -> dict[str, Any]:
    p = envelope(physical, PHYSICAL_KEYS, "physical source")
    lineage_row = envelope(lineage, LINEAGE_KEYS, "lineage source")
    if p["schema"] != "linux-gguf-process-continuity-v1" or (
        p["source_kind"] != "unsigned_host_local_proc"
    ):
        raise ValueError("invalid physical source schema or kind")
    if lineage_row["schema"] != "lmstudio-worker-lineage-observation-v1" or (
        lineage_row["source_kind"] != "unsigned_host_local_linux_process"
    ):
        raise ValueError("invalid lineage source schema or kind")
    for flag in (
        "signature_verified", "model_loaded_attested", "owner_bound",
        "can_approve", "can_dispatch",
    ):
        if p[flag] is not False:
            raise ValueError("physical source cannot declare authority")
    for flag in (
        "physical_model_digest_checked_by_this_tool",
        "model_loaded_inference_attested", "gateway_forwarding_attested",
        "source_signature_verified", "operator_approval_present",
        "owner_bound_request_attested", "can_approve", "can_dispatch",
    ):
        if lineage_row[flag] is not False:
            raise ValueError("lineage source cannot declare authority")
    if p["continuity_checked"] is not True or (
        lineage_row["listener_ownership_observed"] is not True or
        lineage_row["direct_worker_health_ok"] is not True
    ):
        raise ValueError("incomplete process or listener checks")

    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[a-f0-9]{64}", expected_sha256
    ):
        raise ValueError("expected SHA256 must be exact lowercase hex")
    typed_int(expected_bytes, "expected bytes")
    for port in (expected_gateway_port, expected_worker_port):
        if typed_int(port, "port") > 65535:
            raise ValueError("invalid local port")
    now = typed_int(now_ms, "current UTC epoch ms")
    a = typed_int(p["observed_at_ms"], "physical observation time")
    b = typed_int(lineage_row["observed_at_ms"], "lineage observation time")
    for t in (a, b):
        if t > now + CLOCK_SKEW_MS:
            raise ValueError("future-dated host evidence")
        if t + SOURCE_MAX_AGE_MS <= now:
            raise ValueError("host evidence expired")
    if abs(a - b) > CROSS_OBSERVATION_MAX_GAP_MS:
        raise ValueError("physical and lineage observations too far apart")
    if not isinstance(p["boot_id"], str) or p["boot_id"] != lineage_row["boot_id"]:
        raise ValueError("host boot identity mismatch")
    if (typed_int(p["pid"], "model PID") != typed_int(lineage_row["worker_pid"], "worker PID")
            or typed_int(p["process_start_ticks"], "model start ticks")
            != typed_int(lineage_row["worker_process_start_ticks"], "worker start ticks")):
        raise ValueError("physical model process and socket worker differ")
    if (typed_int(lineage_row["worker_parent_pid"], "worker parent PID")
            != typed_int(lineage_row["gateway_pid"], "gateway PID")):
        raise ValueError("LM Studio child identity mismatch")
    if (lineage_row["gateway_listener_port"] != expected_gateway_port
            or lineage_row["worker_listener_port"] != expected_worker_port):
        raise ValueError("gateway or worker port differs from expected")
    if p["model_artifact_fingerprint"] != "sha256:" + expected_sha256 or (
        p["model_bytes"] != expected_bytes
    ):
        raise ValueError("wrong GGUF artifact bound to worker")
    return {
        "schema": "correlated-unsigned-host-model-lineage-v1",
        "status": "CORRELATED_LOCAL_PROCESS_AND_SOCKET_ONLY",
        "observed_at_ms": max(a, b),
        "physical_observation_age_ms": now - a,
        "lineage_observation_age_ms": now - b,
        "boot_id_sha256_prefix": hashlib.sha256(
            p["boot_id"].encode("utf-8")
        ).hexdigest()[:12],
        "worker_pid": p["pid"],
        "worker_start_ticks": p["process_start_ticks"],
        "gateway_pid": lineage_row["gateway_pid"],
        "gateway_start_ticks": lineage_row["gateway_process_start_ticks"],
        "worker_port": lineage_row["worker_listener_port"],
        "gateway_port": lineage_row["gateway_listener_port"],
        "model_sha256": expected_sha256,
        "model_bytes": expected_bytes,
        "source_attested": False,
        "model_loaded_execution_attested": False,
        "gateway_request_forwarding_attested": False,
        "authenticated_owner_receipt": False,
        "operator_approval_present": False,
        "can_approve": False,
        "can_auto_renew": False,
        "can_dispatch": False,
    }


def read_small_json(path: Path) -> dict:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("input files must be explicit regular absolute paths")
    if not 0 < path.stat().st_size <= 8192:
        raise ValueError("input outside bounded JSON envelope")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("physical_json", type=Path)
    parser.add_argument("lineage_json", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-bytes", type=int, required=True)
    parser.add_argument("--gateway-port", type=int, required=True)
    parser.add_argument("--worker-port", type=int, required=True)
    args = parser.parse_args()
    try:
        report = correlate(
            physical=read_small_json(args.physical_json),
            lineage=read_small_json(args.lineage_json),
            expected_sha256=args.expected_sha256,
            expected_bytes=args.expected_bytes,
            expected_gateway_port=args.gateway_port,
            expected_worker_port=args.worker_port,
            now_ms=int(time.time() * 1000),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("unsigned source correlation refused: " +
              type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
