"""Sweep42 — hermetic role-normalization tests for the signed routing projection.

Synthetic, in-memory fixtures only: no Neo4j, router, service, network,
credential, worktree or live-state access.
"""

from __future__ import annotations

import json
from typing import Any

from assistx.fleet_routing_matrix import _list_of_strings
from assistx.fleet_routing_projection import (
    _json_list,
    benchmark_projection_index,
    node_routing_policy_index,
)


class _FakeSession:
    def __init__(self, response_by_marker: dict[str, list[dict[str, Any]]]) -> None:
        self._response_by_marker = response_by_marker

    def run(self, query: str) -> list[dict[str, Any]]:
        for marker, rows in self._response_by_marker.items():
            if marker in query:
                return list(rows)
        return []

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeNeo:
    def __init__(self, response_by_marker: dict[str, list[dict[str, Any]]]) -> None:
        self._response_by_marker = response_by_marker
        self.closed = False

    def _session(self) -> _FakeSession:
        return _FakeSession(self._response_by_marker)

    def close(self) -> None:
        self.closed = True


def _factory(
    response_by_marker: dict[str, list[dict[str, Any]]],
) -> Any:
    def make() -> _FakeNeo:
        return _FakeNeo(response_by_marker)

    return make


def test_json_list_strips_padded_roles_from_json_string() -> None:
    roles = json.dumps(["coder", " reviewer ", "researcher"])
    assert _json_list(roles) == ["coder", "researcher", "reviewer"]


def test_json_list_strips_padded_roles_from_python_list() -> None:
    assert _json_list(["coder", " reviewer "]) == ["coder", "reviewer"]


def test_json_list_drops_blank_entries_and_dedupes_after_strip() -> None:
    assert _json_list([" reviewer ", "reviewer", "  ", "", "coder"]) == [
        "coder",
        "reviewer",
    ]


def test_json_list_agrees_with_canonical_matrix_normalizer() -> None:
    raw = json.dumps([" coder ", "reviewer ", "  ", " reviewer "])
    assert _json_list(raw) == _list_of_strings(json.loads(raw))


def test_json_list_controls_unchanged() -> None:
    assert _json_list(json.dumps(["coder", "reviewer"])) == ["coder", "reviewer"]
    assert _json_list(None) == []
    assert _json_list("   ") == []
    assert _json_list("not-json") == []


def test_node_routing_policy_index_normalizes_roles() -> None:
    neo = _factory(
        {
            "MATCH (n:FleetNode)": [
                {
                    "node_id": "n1",
                    "roles_json": json.dumps([" coder ", " reviewer "]),
                    "worker_mode": "observer_only",
                    "allow_agent_runtime": False,
                    "allow_code_execution": False,
                }
            ]
        }
    )
    policy = node_routing_policy_index(neo)
    assert policy["n1"]["routing_roles"] == ["coder", "reviewer"]


def test_node_routing_policy_index_control_plain_roles() -> None:
    neo = _factory(
        {
            "MATCH (n:FleetNode)": [
                {
                    "node_id": "n2",
                    "roles_json": json.dumps(["coder", "reviewer"]),
                    "worker_mode": "observer_only",
                    "allow_agent_runtime": False,
                    "allow_code_execution": False,
                }
            ]
        }
    )
    policy = node_routing_policy_index(neo)
    assert policy["n2"]["routing_roles"] == ["coder", "reviewer"]


def test_benchmark_projection_index_normalizes_roles() -> None:
    neo = _factory(
        {
            "MATCH (n:FleetNode)": [
                {
                    "node_id": "n1",
                    "roles_json": json.dumps([" coder ", " reviewer "]),
                    "worker_mode": "observer_only",
                    "allow_agent_runtime": False,
                    "allow_code_execution": False,
                }
            ],
            "MATCH (p:BenchmarkRoutingProfile)": [
                {
                    "profile": {
                        "node_id": "n1",
                        "model_id": "m1",
                        "task_family": "coding",
                        "quality_score": 0.9,
                    }
                }
            ],
        }
    )
    index = benchmark_projection_index(neo)
    assert index[("n1", "m1")]["routing_roles"] == ["coder", "reviewer"]
