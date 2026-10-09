"""Offline regression: Graph properties may be strings; only bool True authorizes."""
from __future__ import annotations

import pytest

from assistx.fleet_routing_projection import (
    benchmark_projection_index,
    node_routing_policy_index,
)


class FakeNeo:
    def __init__(self, agent_permission, code_permission):
        self.agent_permission = agent_permission
        self.code_permission = code_permission
        self.closed = False

    def _session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query):
        if "MATCH (n:FleetNode)" in query:
            return [{
                "node_id": "xwing",
                "roles_json": '["summarization"]',
                "worker_mode": "auxiliary",
                "allow_agent_runtime": self.agent_permission,
                "allow_code_execution": self.code_permission,
            }]
        if "MATCH (p:BenchmarkRoutingProfile)" in query:
            return [{
                "profile": {
                    "node_id": "xwing",
                    "model_id": "local/qwen",
                    "task_family": "summarization",
                    "quality_floor_passed": True,
                    "utility_score": 0.75,
                },
            }]
        raise AssertionError("unexpected synthetic query")

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("agent", "code", "expected_agent", "expected_code"),
    [
        (True, True, True, True),
        (False, False, False, False),
        ("true", "false", False, False),
        (1, 1, False, False),
        (None, None, False, False),
    ],
)
def test_graph_routing_permissions_require_literal_boolean_true(
    agent, code, expected_agent, expected_code,
):
    created = []

    def factory():
        neo = FakeNeo(agent, code)
        created.append(neo)
        return neo

    node_policy = node_routing_policy_index(factory)["xwing"]
    bench_policy = benchmark_projection_index(factory)[("xwing", "local/qwen")]
    for policy in (node_policy, bench_policy):
        assert policy["allow_agent_runtime"] is expected_agent
        assert policy["allow_code_execution"] is expected_code
        assert policy["routing_roles"] == ["summarization"]
    assert bench_policy["task_family_scores"]["summarization"]["utility_score"] == 0.75
    assert len(created) == 2
    assert all(neo.closed for neo in created)
