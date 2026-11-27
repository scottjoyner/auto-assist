"""Malformed benchmark quality-floor verdicts must fail closed (r49).

``quality_floor_passed`` is evidence from a benchmark profile, not a trusted
Python boolean. Python truthiness silently upgrades strings such as ``"false"``
or ``"0"``, integers, floats and non-empty containers into a green quality-floor
pass, which admits a model or node into the allocation plan and inflates its
value-matrix score. Only the genuine boolean ``True`` may certify a pass.

These tests are pure and in-process: no socket, Neo4j, Redis, SQLite/DB/WAL,
service, container, credential file or network mount is touched.
"""

from __future__ import annotations

import pytest

from assistx.benchmark_allocation_policy import (
    _enrich_nodes,
    _family_value_matrix,
)

# Values a JSON/config boundary can deliver for a boolean-looking field but
# that are NOT the boolean singleton True. Every one must fail closed.
MALFORMED_FLAGS = [
    "false",
    "0",
    "off",
    "error",
    "False",
    "no",
    1,
    2,
    1.0,
    ["pass"],
    {"status": "failed"},
]


def _policy() -> dict:
    return {
        "optiplex": {
            "node_id": "optiplex",
            "worker_mode": "auxiliary",
            "roles": ["auxiliary_llm", "compression"],
            "allow_agent_runtime": False,
            "allow_code_execution": False,
        }
    }


def _loaded_nodes() -> list[dict]:
    return [
        {
            "hostname": "optiplex",
            "online": True,
            "capabilities": ["llm"],
            "loaded_models": ["failed-fast"],
        }
    ]


def _profiles(value: object) -> dict:
    return {
        ("optiplex", "failed-fast", "compression"): {
            "quality_floor_passed": value,
            "quality_score": 0.2,
        }
    }


@pytest.mark.parametrize("value", MALFORMED_FLAGS)
def test_malformed_flag_never_admits_loaded_model(value: object) -> None:
    enriched = _enrich_nodes(
        _loaded_nodes(), _policy(), "compression", _profiles(value)
    )[0]
    assert enriched["loaded_models"] == []
    assert enriched["quality_floor_rejected_models"] == ["failed-fast"]
    assert enriched["is_blocked"] is True
    assert enriched["control_mode"] == "benchmark_quality_floor_failed"


@pytest.mark.parametrize("value", MALFORMED_FLAGS)
def test_malformed_flag_never_admits_node_without_loaded_models(
    value: object,
) -> None:
    nodes = [{"hostname": "optiplex", "online": True, "capabilities": ["llm"]}]
    enriched = _enrich_nodes(
        nodes, _policy(), "compression", _profiles(value)
    )[0]
    assert enriched["is_blocked"] is True
    assert enriched["control_mode"] == "benchmark_quality_floor_failed"


@pytest.mark.parametrize("value", MALFORMED_FLAGS)
def test_malformed_flag_never_certifies_value_matrix(value: object) -> None:
    matrix = {
        "entries": [{"node_id": "optiplex", "model_id": "failed-fast"}]
    }
    profiles = {
        ("optiplex", "failed-fast", "compression"): {
            "quality_floor_passed": value,
            "quality_score": 0.9,
        }
    }
    row = _family_value_matrix(matrix, profiles, "compression")["entries"][0]
    assert row["benchmark_quality_floor_passed"] is False
    assert row["quality_score"] == 0.0


@pytest.mark.parametrize(
    ("value", "expected_pass"),
    [(True, True), (False, False), (None, False)],
)
def test_genuine_boolean_verdicts_are_preserved(
    value: object, expected_pass: bool
) -> None:
    enriched = _enrich_nodes(
        _loaded_nodes(), _policy(), "compression", _profiles(value)
    )[0]
    if expected_pass:
        assert enriched["loaded_models"] == ["failed-fast"]
        assert enriched.get("is_blocked") is not True
    else:
        assert enriched["loaded_models"] == []
        assert enriched["is_blocked"] is True

    matrix = {
        "entries": [{"node_id": "optiplex", "model_id": "failed-fast"}]
    }
    profiles = {
        ("optiplex", "failed-fast", "compression"): {
            "quality_floor_passed": value,
            "quality_score": 0.9,
        }
    }
    row = _family_value_matrix(matrix, profiles, "compression")["entries"][0]
    assert row["benchmark_quality_floor_passed"] is expected_pass
    assert row["quality_score"] == (0.9 if expected_pass else 0.0)


def test_missing_flag_fails_closed() -> None:
    profiles = {
        ("optiplex", "failed-fast", "compression"): {"quality_score": 0.9}
    }
    enriched = _enrich_nodes(
        _loaded_nodes(), _policy(), "compression", profiles
    )[0]
    assert enriched["loaded_models"] == []
    assert enriched["is_blocked"] is True
