from contextlib import contextmanager

from assistx.evaluation.otel_sdk_adapter import emit_trace_with_otel_sdk
from assistx.evaluation.trace_recorder import benchmark_trace


class FakeSpan:
    def __init__(self, attributes):
        self.attributes = attributes

    def set_attribute(self, key, value):
        self.attributes[key] = value


class FakeTracer:
    def __init__(self):
        self.spans = []

    @contextmanager
    def start_as_current_span(self, name):
        attributes = {"name": name}
        self.spans.append(attributes)
        yield FakeSpan(attributes)


def test_otel_sdk_emitter_preserves_canonical_trace_correlation():
    trace = benchmark_trace(
        task_id="task-otel",
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=True,
        validation_passed=True,
    )
    tracer = FakeTracer()
    emitted = emit_trace_with_otel_sdk(trace, tracer=tracer)
    assert emitted == len(trace["spans"]) + 1
    assert tracer.spans[0]["name"] == "assistx.run"
    assert tracer.spans[0]["assistx.canonical_trace_id"] == trace["trace_id"]
    assert tracer.spans[2]["name"] == "assistx.route"
    assert tracer.spans[2]["assistx.node_id"] == "x1-370"
