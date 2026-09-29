#!/usr/bin/env python3
"""Emit one AssistX trace through the real OpenTelemetry Python SDK."""

from __future__ import annotations

import json

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from assistx.evaluation.otel_sdk_adapter import emit_trace_with_otel_sdk
from assistx.evaluation.trace_recorder import benchmark_trace


def main() -> int:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("assistx-experiment-smoke")

    trace = benchmark_trace(
        task_id="otel-smoke",
        node_id="ci-fixture",
        model="fixture-model",
        task_family="coding",
        success=True,
        validation_passed=True,
        experiment={
            "name": "otel-compatibility",
            "variant": "sdk-1.44.0",
            "baseline_id": "assistx-trace-v1",
            "source_commit": "ci",
        },
    )
    emitted = emit_trace_with_otel_sdk(trace, tracer=tracer)
    finished = exporter.get_finished_spans()
    if len(finished) != emitted:
        raise SystemExit(f"expected {emitted} exported spans, got {len(finished)}")
    if not any(span.name == "assistx.run" for span in finished):
        raise SystemExit("root AssistX span missing")
    if not all(span.attributes.get("assistx.canonical_trace_id") == trace["trace_id"] for span in finished):
        raise SystemExit("canonical trace correlation missing")

    print(
        json.dumps(
            {
                "emitted_spans": emitted,
                "canonical_trace_id": trace["trace_id"],
                "span_names": [span.name for span in finished],
                "sdk_version": "1.44.0",
            },
            sort_keys=True,
        )
    )
    provider.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
