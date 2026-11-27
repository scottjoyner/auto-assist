"""Fail-closed regression for malformed ASSISTX_EVALUATION_SUITES thresholds (r46).

Hermetic: an environment string goes in and a registry comes out. No socket,
HTTP, Neo4j, Redis, SQLite/DB/WAL, network mount, credential, service or live
state is touched, and no secret value is asserted or printed.
"""

from __future__ import annotations

import pytest

from assistx import evaluation_registry as er

OVERRIDE = "strict_gate|quality_analyst|true|daily|{threshold}|description"


@pytest.fixture
def baseline(monkeypatch):
    monkeypatch.delenv("ASSISTX_EVALUATION_SUITES", raising=False)
    return er.get_evaluation_suites()


def _set_override(monkeypatch, threshold: str) -> None:
    monkeypatch.setenv(
        "ASSISTX_EVALUATION_SUITES", OVERRIDE.format(threshold=threshold)
    )


@pytest.mark.parametrize(
    "bad_threshold",
    [
        "",
        " ",
        "not-a-number",
        "0.9x",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "Infinity",
        "-1",
        "1.7",
        "1e309",
    ],
)
def test_malformed_threshold_falls_back_to_builtins(
    monkeypatch, baseline, bad_threshold
):
    _set_override(monkeypatch, bad_threshold)
    suites = er.get_evaluation_suites()
    assert [s.name for s in suites] == [s.name for s in baseline]
    assert not any(s.name == "strict_gate" for s in suites)


def test_malformed_threshold_never_fabricates_a_weakened_floor(monkeypatch):
    _set_override(monkeypatch, "bogus")
    assert 0.8 not in {s.threshold for s in er.get_evaluation_suites()}


def test_valid_threshold_override_is_honored(monkeypatch):
    _set_override(monkeypatch, "0.97")
    suites = er.get_evaluation_suites()
    assert len(suites) == 1
    assert suites[0].name == "strict_gate"
    assert suites[0].threshold == 0.97
    assert suites[0].enabled is True


def test_boundary_thresholds_are_honored(monkeypatch):
    for value in ("0", "1"):
        _set_override(monkeypatch, value)
        suites = er.get_evaluation_suites()
        assert len(suites) == 1
        assert suites[0].threshold == float(value)


def test_explicit_disable_override_is_honored(monkeypatch):
    monkeypatch.setenv(
        "ASSISTX_EVALUATION_SUITES",
        "strict_gate|quality_analyst|false|daily|0.95|description",
    )
    suites = er.get_evaluation_suites()
    assert len(suites) == 1
    assert suites[0].enabled is False
    assert suites[0].threshold == 0.95


def test_truncated_entry_still_falls_back(monkeypatch, baseline):
    monkeypatch.setenv(
        "ASSISTX_EVALUATION_SUITES", "strict_gate|quality_analyst|true|daily"
    )
    assert [s.name for s in er.get_evaluation_suites()] == [
        s.name for s in baseline
    ]


def test_suites_summary_reflects_the_fallback(monkeypatch, baseline):
    _set_override(monkeypatch, "invalid")
    summary = er.suites_summary()
    assert summary["total"] == len(baseline)
    assert {s["name"] for s in summary["suites"]} == {
        s.name for s in baseline
    }
