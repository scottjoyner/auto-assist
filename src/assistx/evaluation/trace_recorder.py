"""Local-first trace recording for AssistX experiments."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .trace_schema import TRACE_SCHEMA_VERSION, canonical_json, validate_trace


@dataclass
class TraceRecorder:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    spans: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def add_span(self, span_type: str, **attributes: Any) -> None:
        self.spans.append({"type": span_type, "attributes": attributes})

    def finish(self, status: str, *, evidence_ids: list[str] | None = None, **metadata: Any) -> dict[str, Any]:
        trace = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "spans": list(self.spans),
            "outcome": {
                "status": status,
                "evidence_ids": list(evidence_ids or []),
                "duration_ms": round((time.time() - self.started_at) * 1000, 3),
                **metadata,
            },
        }
        validate_trace(trace)
        return trace


def append_trace_jsonl(trace: dict[str, Any], path: str | os.PathLike[str] | None = None) -> str | None:
    """Append one redacted canonical trace to JSONL when explicitly configured."""
    target = str(path or os.getenv("ASSISTX_TRACE_JSONL_PATH", "")).strip()
    if not target:
        return None
    payload = canonical_json(trace, redact=True)
    file_path = Path(target)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    return str(file_path)


def benchmark_trace(
    *,
    task_id: str,
    node_id: str,
    model: str,
    task_family: str,
    success: bool,
    validation_passed: bool | None,
    evidence_ids: list[str] | None = None,
    tokens_per_second: float | None = None,
) -> dict[str, Any]:
    """Build the first concrete AssistX trace shape for adaptive benchmarks."""
    recorder = TraceRecorder(trace_id=f"benchmark-{task_id}")
    recorder.add_span("task", task_id=task_id, task_family=task_family, benchmark=True)
    recorder.add_span("route", node_id=node_id, network_path="local_or_tailnet")
    recorder.add_span("model", model=model, node_id=node_id, tokens_per_second=tokens_per_second)
    recorder.add_span("test", validation_passed=validation_passed, benchmark_family=task_family)
    return recorder.finish(
        "success" if success else "failure",
        evidence_ids=evidence_ids or ([f"benchmark-outcome:{task_id}"] if success else []),
    )
