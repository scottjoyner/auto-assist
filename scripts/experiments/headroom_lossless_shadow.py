#!/usr/bin/env python3
"""Synthetic-only, offline-first Headroom lossless-densify research probe.

Do not install dependencies, start a proxy, modify agent settings, or read
live prompts/traces. Run only inside an externally enforced no-network,
read-only, non-root container using verified package wheels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any

UPSTREAM = "headroomlabs-ai/headroom"
VERSION = "0.42.0"
MARKER = "FATAL_TRACE_HASH=8a593fac2bca17c4"
LOCATION = "src/retrieval.rs:87"
OFFLINE_ENV = {
    "HEADROOM_OFFLINE": "1",
    "HEADROOM_BEACON": "off",
    "HEADROOM_TELEMETRY": "off",
    "DO_NOT_TRACK": "1",
}
MAX_BYTES = 300_000


class EvidenceRejected(ValueError):
    pass


def synthetic_cases() -> dict[str, str]:
    records = [
        {
            "time": f"2026-11-02T12:00:{i%60:02}",
            "module": "mod_long_name",
            "status": "PASS",
            "id": i,
            "trace": "synthetic",
            "detail": "long repeated status field this is a synthetic fixture",
        }
        for i in range(440)
    ]
    records.append({
        "time": "2026-11-02T13:13:13",
        "status": "FAIL",
        "trace": MARKER,
        "source": LOCATION,
        "detail": "signed-witness mismatch",
    })
    return {
        "pretty_json": json.dumps({"records": records}, indent=2),
        "ndjson": "\n".join(json.dumps(row) for row in records),
        "cargo_tests": "\n".join(
            f"test synthetic::case_{i:04} ... ok" for i in range(350)
        ) + f"\nthread panicked at {LOCATION}\n{MARKER} signed-witness mismatch\n",
        "application_log": "\n".join(
            f"2026-11-02T12:00:{i%60:02} INFO tick={i} status=healthy model=synthetic"
            for i in range(440)
        ) + f"\nFATAL {MARKER} signed-witness mismatch at {LOCATION}\n",
    }


def require_offline(env: dict[str, str] | Any) -> None:
    for name, expected in OFFLINE_ENV.items():
        if env.get(name) != expected:
            raise EvidenceRejected("offline and telemetry controls are mandatory")
    # Even with these flags, tokenizer code may try network; the caller must
    # also enforce container --network none and deny outbound traffic.


def assess(name: str, source: str, result: str, lossless: bool, reverted: int,
           duration_ms: float, tokens_before: int, tokens_after: int) -> dict:
    if not name or name not in synthetic_cases() or type(source) is not str or type(result) is not str:
        raise EvidenceRejected("unexpected synthetic case")
    if source != synthetic_cases()[name]:
        raise EvidenceRejected("not the canonical synthetic fixture")
    size = len(source.encode("utf-8"))
    end_size = len(result.encode("utf-8"))
    if not 0 < size <= MAX_BYTES or end_size > MAX_BYTES or type(lossless) is not bool:
        raise EvidenceRejected("invalid output size or lossless flag")
    if type(reverted) is not int or not 0 <= reverted <= 3:
        raise EvidenceRejected("invalid reversion count")
    if type(duration_ms) not in (float, int) or not 0 <= duration_ms <= 60_000:
        raise EvidenceRejected("invalid timing")
    if type(tokens_before) is not int or type(tokens_after) is not int or min(tokens_before,tokens_after) < 0:
        raise EvidenceRejected("invalid reported token estimates")
    marker_preserved = MARKER in result
    location_preserved = LOCATION in result
    unchanged = source == result
    # Library's claimed lossless signal alone cannot attest that changed bytes
    # reconstruct to the source. Preserve content unless exact equality holds.
    evidence_retained = unchanged and lossless and marker_preserved and location_preserved
    return {
        "kind": "headroom_lossless_shadow_v1",
        "candidate": UPSTREAM,
        "upstream_version": VERSION,
        "synthetic_case": name,
        "source_bytes": size,
        "output_bytes": end_size,
        "byte_reduction_pct": round(100 * (1-end_size/size), 3),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
        "source_unchanged": unchanged,
        "marker_preserved": marker_preserved,
        "source_location_preserved": location_preserved,
        "library_reported_lossless": lossless,
        "library_reverted_messages": reverted,
        "library_estimated_tokens_before": tokens_before,
        "library_estimated_tokens_after": tokens_after,
        "estimated_tokens_saved": tokens_before-tokens_after,
        "actual_model_tokens_verified": False,
        "elapsed_ms": round(duration_ms, 3),
        "evidence_roundtrip_independently_verified": unchanged,
        "synthetic_evidence_gate_passed": evidence_retained,
        "live_prompt_or_trace_used": False,
        "telemetry_disabled_by_environment": True,
        "external_network_isolation_independently_verified": False,
        "production_gate": "HOLD",
        "deployment_allowed": False,
        "automated_agent_hooks_allowed": False,
        "claims_custody_authorized": False,
        "note": "Passing on unchanged content proves no change, not useful compression; changed content requires a separate exact decoder/roundtrip gate.",
    }


def run() -> list[dict]:
    require_offline(os.environ)
    # Never import upstream on default invocation. Libraries and their native
    # dependency graph execute only with deliberate --execute-synthetic.
    import headroom
    if getattr(headroom, "__version__", None) != VERSION:
        raise EvidenceRejected("wrong upstream version")
    out = []
    for name, source in synthetic_cases().items():
        original = [
            {"role": "user", "content": "Analyze"},
            {"role": "tool", "content": source},
            {"role": "assistant", "content": "Checking"},
        ]
        start = time.perf_counter()
        result = headroom.densify(
            original, model="gpt-4o",
            protect_recent=0, min_tokens_to_compress=25,
            compress_system_messages=False,
        )
        elapsed = (time.perf_counter()-start)*1000
        messages = getattr(result, "messages", None)
        if not isinstance(messages,list) or len(messages) != 3:
            raise EvidenceRejected("message cardinality changed")
        for index in (0,2):
            if messages[index] != original[index]:
                raise EvidenceRejected("non-tool message modified")
        compressed = messages[1].get("content")
        if type(compressed) is not str:
            raise EvidenceRejected("tool content was not text")
        out.append(assess(
            name, source, compressed, result.lossless,
            result.reverted_messages, elapsed,
            result.tokens_before, result.tokens_after,
        ))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-synthetic", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_synthetic:
        print("Refused: explicit --execute-synthetic required", file=sys.stderr)
        return 2
    try:
        cases = run()
    except (ImportError, EvidenceRejected, OSError, RuntimeError, TypeError, ValueError):
        print("Headroom shadow rejected or incomplete", file=sys.stderr)
        return 2
    # Deliberately never print raw prompts, marker values, text, or traces.
    for item in cases:
        print(json.dumps(item, sort_keys=True, allow_nan=False))
    return 0 if all(x["synthetic_evidence_gate_passed"] for x in cases) else 3


if __name__ == "__main__":
    raise SystemExit(main())
