"""Harness-suite registry must fail closed on malformed env overrides (sweep34x).

``get_harness_suites()`` documents that invalid overrides fall back to the
built-in registry. A malformed typed field (e.g. a non-integer ``min_trials``
or a non-iterable ``task_families``) must therefore be skipped, not escape as
an exception from the registry lookup -- that lookup gates promotion evidence
and benchmark task construction.
"""

import json

from assistx import evaluation_registry as er


def _env(monkeypatch, value):
    monkeypatch.setenv("ASSISTX_HARNESS_SUITES", json.dumps(value))


def test_non_integer_min_trials_falls_back_to_builtin(monkeypatch):
    _env(
        monkeypatch,
        [
            {
                "id": "bad.mintrials.v1",
                "schema_version": "x",
                "task_families": ["tool_use"],
                "min_trials": "three",
            }
        ],
    )
    assert er.get_harness_suites() == list(er.DEFAULT_HARNESS_SUITES)


def test_non_integer_min_trials_keeps_valid_siblings(monkeypatch):
    _env(
        monkeypatch,
        [
            {
                "id": "good.suite.v1",
                "schema_version": "x",
                "task_families": ["summarization"],
                "min_trials": 2,
            },
            {
                "id": "bad.mintrials.v1",
                "schema_version": "x",
                "task_families": ["coding"],
                "min_trials": "three",
            },
        ],
    )
    assert [s.id for s in er.get_harness_suites()] == ["good.suite.v1"]


def test_non_iterable_task_families_falls_back_to_builtin(monkeypatch):
    _env(
        monkeypatch,
        [
            {
                "id": "bad.families.v1",
                "schema_version": "x",
                "task_families": 7,
            }
        ],
    )
    assert er.get_harness_suites() == list(er.DEFAULT_HARNESS_SUITES)


def test_suite_for_family_survives_malformed_override(monkeypatch):
    _env(
        monkeypatch,
        [
            {
                "id": "bad.mintrials.v1",
                "task_families": ["tool_use"],
                "min_trials": "three",
            }
        ],
    )
    suite = er.suite_for_family("tool_use")
    assert suite is not None
    assert suite.id == "hermes_agent_intelligence.v1"
    assert suite.min_trials == 3


def test_valid_override_is_unchanged(monkeypatch):
    _env(
        monkeypatch,
        [
            {
                "id": "custom.v2",
                "schema_version": "x",
                "task_families": ["coding"],
                "min_trials": 2,
            }
        ],
    )
    suites = er.get_harness_suites()
    assert [s.id for s in suites] == ["custom.v2"]
    assert suites[0].min_trials == 2
