"""Harness suite registry: the agent-harness benchmark suites from the harness
version registry are declared as first-class defs, mappable to benchmark task
families, overridable via env."""

import json

from assistx import evaluation_registry
from assistx.benchmark_controller import BenchmarkController


def test_default_harness_suites_are_registered():
    ids = {s.id for s in evaluation_registry.get_harness_suites()}
    assert {
        "hermes_agent_intelligence.v1",
        "hermes_agent_context_pressure.v1",
        "agent_skill_suite.v1",
        "cpm-tb2-bench-v1",
        "terminal_bench_4.0",
    } <= ids


def test_hermes_qualification_suites_require_three_trials():
    by_id = {s.id: s for s in evaluation_registry.get_harness_suites()}
    assert by_id["hermes_agent_intelligence.v1"].min_trials == 3
    assert by_id["hermes_agent_context_pressure.v1"].min_trials == 3
    assert by_id["agent_skill_suite.v1"].min_trials == 1


def test_suite_ids_and_schema_versions_are_separate_fields():
    by_id = {s.id: s for s in evaluation_registry.get_harness_suites()}
    assert by_id["agent_skill_suite.v1"].schema_version == "2"
    assert by_id["hermes_agent_intelligence.v1"].schema_version == "hermes_agent_suite.v1"


def test_suite_for_family_precedence():
    # Hermes qualification suite owns tool_use/reasoning (declaration order).
    assert evaluation_registry.suite_for_family("tool_use").id == "hermes_agent_intelligence.v1"
    assert evaluation_registry.suite_for_family("reasoning").id == "hermes_agent_intelligence.v1"
    assert (
        evaluation_registry.suite_for_family("long_context").id
        == "hermes_agent_context_pressure.v1"
    )
    assert evaluation_registry.suite_for_family("coding").id == "agent_skill_suite.v1"
    assert evaluation_registry.suite_for_family("summarization").id == "agent_skill_suite.v1"
    assert evaluation_registry.suite_for_family("nonexistent") is None
    assert evaluation_registry.suite_for_family("") is None


def test_env_override_extends_registry(monkeypatch):
    override = [
        {
            "id": "custom_edge_suite.v2",
            "schema_version": "edge-2",
            "task_families": ["summarization"],
            "min_trials": 2,
            "source_repo": "agent-harness",
            "description": "Local override",
        }
    ]
    monkeypatch.setenv("ASSISTX_HARNESS_SUITES", json.dumps(override))
    suites = evaluation_registry.get_harness_suites()
    assert [s.id for s in suites] == ["custom_edge_suite.v2"]
    assert suites[0].min_trials == 2
    assert evaluation_registry.suite_for_family("summarization").id == "custom_edge_suite.v2"


def test_invalid_env_override_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("ASSISTX_HARNESS_SUITES", "{not json")
    assert evaluation_registry.get_harness_suites() == list(
        evaluation_registry.DEFAULT_HARNESS_SUITES
    )


def test_benchmark_task_payload_carries_suite_id():
    controller = BenchmarkController()
    task = controller._task(
        {
            "task_family": "long_context",
            "node_id": "x1-370",
            "model_id": "lmstudio-x1-370.qwen3.5-9b-neo-heretic-i1",
            "requires_model_load": False,
            "execution_mode": "dry_run",
        }
    )
    assert task is not None
    assert task["payload"]["suite_id"] == "hermes_agent_context_pressure.v1"


def test_benchmark_task_payload_suite_id_none_when_uncovered(monkeypatch):
    # An override registry that covers no families leaves suite_id None.
    monkeypatch.setenv("ASSISTX_HARNESS_SUITES", json.dumps([
        {"id": "unrelated.suite", "task_families": ["exotic_family"]},
    ]))
    controller = BenchmarkController()
    task = controller._task(
        {
            "task_family": "long_context",
            "node_id": "x1-370",
            "model_id": "m",
            "requires_model_load": False,
            "execution_mode": "dry_run",
        }
    )
    assert task is not None
    assert task["payload"]["suite_id"] is None
