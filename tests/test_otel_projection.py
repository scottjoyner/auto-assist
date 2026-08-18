from assistx.evaluation.otel_projection import project_trace_to_otel
from assistx.evaluation.trace_recorder import benchmark_trace


def test_otel_projection_preserves_trace_and_span_semantics():
    trace = benchmark_trace(
        task_id="task-1",
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=True,
        validation_passed=True,
        experiment={
            "name": "graph-context",
            "variant": "shadow",
            "baseline_id": "raw",
            "source_commit": "abc123",
            "manifest_id": "manifest-1",
        },
    )
    spans = project_trace_to_otel(trace)
    assert spans[0].name == "assistx.run"
    assert len(spans[0].trace_id_hex) == 32
    assert len(spans[0].span_id_hex) == 16
    assert spans[0].attributes["assistx.experiment.name"] == "graph-context"
    assert spans[1].parent_span_id_hex == spans[0].span_id_hex
    assert spans[1].name == "assistx.task"
    assert spans[2].name == "assistx.route"
    assert spans[2].attributes["assistx.node_id"] == "x1-370"


def test_otel_projection_is_stable_for_same_trace_id():
    trace_a = benchmark_trace(
        task_id="same",
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=True,
        validation_passed=True,
    )
    trace_b = benchmark_trace(
        task_id="same",
        node_id="xwing",
        model="other",
        task_family="reasoning",
        success=True,
        validation_passed=True,
    )
    assert project_trace_to_otel(trace_a)[0].trace_id_hex == project_trace_to_otel(trace_b)[0].trace_id_hex
