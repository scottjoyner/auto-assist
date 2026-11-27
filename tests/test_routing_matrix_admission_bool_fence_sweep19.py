"""Hermetic regression tests for sweep19 admission-boolean coercion fence.

Everything here is an in-process dict. No HTTP, Redis, Neo4j, docker, DB, WAL,
credential file, recovery-hold file, or network mount is read or written; the
root ``tests/conftest.py`` Neo4j integration fixtures are not requested.
"""

from __future__ import annotations

import pytest

from assistx.fleet_routing_matrix import FleetRoutingMatrixError, validate_matrix

_NODE_BOOL_FIELDS = (
    "allow_code_execution",
    "allow_agent_runtime",
    "online",
    "tailnet_discovered",
)


def _matrix(node: dict, profiles: list | None = None) -> dict:
    """Minimal, otherwise-valid routing matrix with one under-test node row."""
    row = {"node_id": "n1"}
    row.update(node)
    return {
        "schema_version": "fleet_routing_matrix.v1",
        "generated_at_utc": "2026-11-26T00:00:00Z",
        "nodes": [row],
        "profiles": profiles or [],
        "rankings": {},
    }


@pytest.mark.parametrize("field", _NODE_BOOL_FIELDS)
@pytest.mark.parametrize("denial", ["false", "0", "no", ""])
def test_explicit_string_denial_is_rejected(field: str, denial: str) -> None:
    """An explicit denial value must never be coerced into an admission of True."""
    with pytest.raises(FleetRoutingMatrixError):
        validate_matrix(_matrix({field: denial}))


@pytest.mark.parametrize("field", _NODE_BOOL_FIELDS)
def test_real_boolean_denial_is_preserved(field: str) -> None:
    """Control: genuine JSON booleans keep their exact meaning."""
    out = validate_matrix(_matrix({field: False}))
    assert out["nodes"][0][field] is False


def test_real_boolean_admission_is_preserved() -> None:
    """Control: a genuine True stays True and the row still validates."""
    out = validate_matrix(
        _matrix(
            {
                "allow_code_execution": True,
                "allow_agent_runtime": True,
                "online": True,
                "tailnet_discovered": True,
            }
        )
    )
    for field in _NODE_BOOL_FIELDS:
        assert out["nodes"][0][field] is True


def test_missing_fields_keep_existing_defaults() -> None:
    """Control: omitted fields are untouched (a separate, prior-review surface)."""
    out = validate_matrix(_matrix({}))
    node = out["nodes"][0]
    assert node["tailnet_discovered"] is True
    assert node["allow_agent_runtime"] is False
    assert node["allow_code_execution"] is False
    assert node["online"] is False


def test_profile_quality_floor_string_denial_is_rejected() -> None:
    """The profile verdict boolean must not coerce a denial string to True."""
    doc = _matrix(
        {},
        profiles=[
            {
                "node_id": "n1",
                "model_id": "m1",
                "task_family": "coding",
                "quality_floor_passed": "false",
            }
        ],
    )
    with pytest.raises(FleetRoutingMatrixError):
        validate_matrix(doc)


def test_profile_quality_floor_real_boolean_is_preserved() -> None:
    """Control: a genuine boolean profile verdict still validates."""
    doc = _matrix(
        {},
        profiles=[
            {
                "node_id": "n1",
                "model_id": "m1",
                "task_family": "coding",
                "quality_floor_passed": False,
            }
        ],
    )
    out = validate_matrix(doc)
    assert out["profiles"][0]["quality_floor_passed"] is False
