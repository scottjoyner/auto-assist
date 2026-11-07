"""Synthetic corrupt/mixed Task priority values cannot crash operator polling.

These tests run an isolated fake graph session, never live Neo4j or credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
import math

import pytest

from assistx.neo4j_client import Neo4jClient


class FakeSession:
    def __init__(self, tasks):
        self.tasks = tasks
        self.queries = []

    def run(self, query, params):
        self.queries.append((query, params))
        return [{"t": task} for task in self.tasks]


def read_tasks(tasks, *, capabilities=None, agent_id=None, limit=20):
    fake = FakeSession(tasks)
    client = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def session():
        yield fake

    client._session = session
    output = client.list_agent_tasks(status="READY", capabilities=capabilities,
                                     agent_id=agent_id, limit=limit)
    return output, fake.queries


def test_nonstring_priorities_are_unset_and_do_not_raise():
    tasks = [
        {"id": "numeric", "priority": 7, "created_at_ts": 30},
        {"id": "dictionary", "priority": {"level": "HIGH"}, "created_at_ts": 25},
        {"id": "array", "priority": ["HIGH"], "created_at_ts": 20},
        {"id": "critical", "priority": "critical", "created_at_ts": 99},
        {"id": "high", "priority": "HIGH", "created_at_ts": 10},
        {"id": "medium", "priority": "MEDIUM", "created_at_ts": 10},
        {"id": "none", "priority": None, "created_at_ts": 15},
    ]
    result, queries = read_tasks(tasks)
    assert [t["id"] for t in result] == [
        "critical", "high", "medium", "none", "array", "dictionary", "numeric"
    ]
    assert len(queries) == 1
    assert queries[0][1] == {"status": "READY", "limit": 200}


def test_created_timestamp_mixed_types_do_not_break_comparison():
    tasks = [
        {"id": "string-ts", "priority": "HIGH", "created_at_ts": "not-a-number"},
        {"id": "numeric-ts", "priority": "HIGH", "created_at_ts": 4},
        {"id": "nan-ts", "priority": "HIGH", "created_at_ts": float("nan")},
        {"id": "inf-ts", "priority": "HIGH", "created_at_ts": float("inf")},
        {"id": "negative-inf", "priority": "HIGH", "created_at_ts": float("-inf")},
        {"id": "bool-ts", "priority": "HIGH", "created_at_ts": True},
        {"id": "missing-ts", "priority": "HIGH"},
    ]
    result, _ = read_tasks(tasks)
    assert [x["id"] for x in result] == [
        "string-ts", "nan-ts", "inf-ts", "negative-inf", "bool-ts",
        "missing-ts", "numeric-ts"
    ]


def test_valid_legacy_priority_and_timestamp_order_unchanged():
    tasks = [
        {"id": "low", "priority": "LOW", "created_at_ts": 1.25},
        {"id": "high-recent", "priority": "HIGH", "created_at_ts": 17},
        {"id": "unset", "created_at_ts": 0},
        {"id": "high-older", "priority": "high", "created_at_ts": 3},
        {"id": "critical", "priority": "CRITICAL", "created_at_ts": 50},
    ]
    result, _ = read_tasks(tasks)
    assert [t["id"] for t in result] == [
        "critical", "high-older", "high-recent", "low", "unset"
    ]


def test_existing_capability_target_and_paperclip_fences_remain():
    tasks = [
        {"id": "good", "priority": 10, "required_capabilities": ["script"]},
        {"id": "wrong-target", "target_agent_id": "elsewhere", "priority": "HIGH"},
        {"id": "paperclip", "paperclip_dispatched": True, "priority": "HIGH"},
        {"id": "no-capability", "required_capabilities": ["llm"], "priority": "HIGH"},
        {"id": "correct-target", "target_agent_id": "synthetic-1", "priority": "MEDIUM"},
    ]
    results, queries = read_tasks(tasks, capabilities=["script"], agent_id="synthetic-1")
    assert [x["id"] for x in results] == ["correct-target", "good"]
    assert queries[0][1] == {"status": "READY", "caps": ["script"], "limit": 200}


def test_existing_limit_caps_result_count():
    tasks = [{"id": str(i), "priority": i} for i in range(40)]
    result, queries = read_tasks(tasks, limit=3)
    assert len(result) == 3
    assert queries[0][1]["limit"] == 30
