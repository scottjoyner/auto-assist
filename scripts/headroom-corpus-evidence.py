#!/usr/bin/env python3
"""Compare raw, lossless JSON, Headroom, and safe hybrid compression."""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import tiktoken

from assistx.context_compression_policy import compress_messages_hybrid
from assistx.context_optimization import compact_json_text
from assistx.headroom_adapter import compress_messages_with_headroom


@dataclass(frozen=True)
class CorpusCaseResult:
    case_id: str
    raw_tokens: int
    stdlib_tokens: int
    headroom_tokens: int
    hybrid_tokens: int
    stdlib_reduction_ratio: float
    headroom_reduction_ratio: float
    hybrid_reduction_ratio: float
    headroom_duration_ms: float
    hybrid_duration_ms: float
    hybrid_strategy: str
    required_markers: tuple[str, ...]
    headroom_missing_required_markers: tuple[str, ...]
    hybrid_missing_required_markers: tuple[str, ...]
    transforms_applied: tuple[str, ...]


def _count_messages(messages, encoding) -> int:
    return len(encoding.encode(json.dumps(messages, sort_keys=True, ensure_ascii=False)))


def _cases():
    exact_uuid = "9d4db0ee-f3d4-4b7f-8cb9-992cdbe24c91"
    exact_sha = "a7d9f2936ae1352b040d3388f98d35bd9b7680f9fd1b7b8fbb81464557b855a6"
    exact_ts = "2026-08-17T20:54:13-04:00"
    exact_path = "/mnt/SSD_4TB/neo4j/backups/neo4j-20260817.dump"
    exact_ip = "100.91.22.14"
    records = [
        {"row": i, "status": "ok", "node": "x1-370", "message": "routine repeated telemetry record", "latency_ms": 100 + (i % 7)}
        for i in range(160)
    ]
    records[87] = {
        "row": 87,
        "status": "critical",
        "uuid": exact_uuid,
        "sha256": exact_sha,
        "timestamp": exact_ts,
        "backup_path": exact_path,
        "tailscale_ip": exact_ip,
        "message": "preserve this incident evidence exactly",
    }
    exact_messages = [
        {"role": "system", "content": "Analyze the tool result conservatively. Do not invent evidence."},
        {"role": "user", "content": "Find the critical incident record and preserve every exact identifier from that record."},
        {"role": "tool", "tool_call_id": "incident_scan", "content": json.dumps({"records": records}, indent=2)},
    ]

    warnings = [
        {"service": f"service-{i % 8}", "status": "warning" if i in {13, 71, 155} else "healthy", "detail": "repeated health detail", "attempt": i}
        for i in range(220)
    ]
    warning_messages = [
        {"role": "system", "content": "Summarize operational tool output."},
        {"role": "user", "content": "Identify all warning rows and their exact service names."},
        {"role": "tool", "tool_call_id": "health", "content": json.dumps({"checks": warnings}, indent=2)},
    ]

    escaped_marker = 'literal\\nnot-newline:"quoted"'
    escaped_payload = {
        "items": [{"id": i, "payload": "ordinary escaped payload"} for i in range(100)],
        "target": {"id": 999, "payload": escaped_marker},
    }
    escaped_messages = [
        {"role": "system", "content": "Inspect structured JSON."},
        {"role": "user", "content": "Return the target payload exactly as represented by the tool result."},
        {"role": "tool", "tool_call_id": "escaped", "content": json.dumps(escaped_payload, indent=2)},
    ]
    return [
        ("exact-identifiers", exact_messages, (exact_uuid, exact_sha, exact_ts, exact_path, exact_ip)),
        ("warning-selection", warning_messages, ("service-5", "service-7", "service-3")),
        ("escaped-string", escaped_messages, (escaped_marker,)),
    ]


def main() -> int:
    encoding = tiktoken.encoding_for_model("gpt-4o")
    results: list[CorpusCaseResult] = []
    for case_id, raw_messages, required_markers in _cases():
        raw_tokens = _count_messages(raw_messages, encoding)
        stdlib_messages = [dict(message) for message in raw_messages]
        stdlib_messages[-1]["content"] = compact_json_text(str(stdlib_messages[-1]["content"])).content
        stdlib_tokens = _count_messages(stdlib_messages, encoding)

        started = time.perf_counter()
        headroom = compress_messages_with_headroom(raw_messages, model="gpt-4o")
        headroom_duration_ms = (time.perf_counter() - started) * 1000
        headroom_serialized = json.dumps(headroom.messages, ensure_ascii=False)
        headroom_missing = tuple(marker for marker in required_markers if marker not in headroom_serialized)

        started = time.perf_counter()
        hybrid = compress_messages_hybrid(raw_messages, model="gpt-4o")
        hybrid_duration_ms = (time.perf_counter() - started) * 1000
        hybrid_tokens = _count_messages(hybrid.messages, encoding)
        hybrid_serialized = json.dumps(hybrid.messages, ensure_ascii=False)
        hybrid_missing = tuple(marker for marker in required_markers if marker not in hybrid_serialized)

        results.append(
            CorpusCaseResult(
                case_id=case_id,
                raw_tokens=raw_tokens,
                stdlib_tokens=stdlib_tokens,
                headroom_tokens=headroom.tokens_after,
                hybrid_tokens=hybrid_tokens,
                stdlib_reduction_ratio=1.0 - (stdlib_tokens / raw_tokens),
                headroom_reduction_ratio=1.0 - (headroom.tokens_after / headroom.tokens_before),
                hybrid_reduction_ratio=1.0 - (hybrid_tokens / raw_tokens),
                headroom_duration_ms=headroom_duration_ms,
                hybrid_duration_ms=hybrid_duration_ms,
                hybrid_strategy=hybrid.strategy,
                required_markers=required_markers,
                headroom_missing_required_markers=headroom_missing,
                hybrid_missing_required_markers=hybrid_missing,
                transforms_applied=headroom.transforms_applied,
            )
        )

    payload = {
        "schema_version": "assistx.headroom-corpus-evidence.v2",
        "headroom_version": "0.32.1",
        "tokenizer_model": "gpt-4o",
        "cases": [asdict(result) for result in results],
        "case_count": len(results),
        "headroom_fidelity_failure_count": sum(bool(r.headroom_missing_required_markers) for r in results),
        "hybrid_fidelity_failure_count": sum(bool(r.hybrid_missing_required_markers) for r in results),
        "mean_stdlib_reduction_ratio": statistics.mean(r.stdlib_reduction_ratio for r in results),
        "mean_headroom_reduction_ratio": statistics.mean(r.headroom_reduction_ratio for r in results),
        "mean_hybrid_reduction_ratio": statistics.mean(r.hybrid_reduction_ratio for r in results),
        "median_headroom_duration_ms": statistics.median(r.headroom_duration_ms for r in results),
        "median_hybrid_duration_ms": statistics.median(r.hybrid_duration_ms for r in results),
        "authoritative_behavior_changed": False,
    }
    Path("headroom-corpus-evidence.json").write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
