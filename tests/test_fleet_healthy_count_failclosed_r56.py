"""Fail-closed regression for fleet.healthy_node_count (r56).

The caller (agents/hermes_agent_adapter._launch_selftasks) clamps background
self-task concurrency to the number of *healthy* nodes precisely so a partial
fleet outage does not overload the survivors. An unverifiable health inventory
must therefore never be reported as "all nodes healthy".

All tests are pure in-process: no socket, DB/WAL, credential, service, model
load, container or network mount. No file is written.
"""

from __future__ import annotations

import pytest

from assistx import fleet


@pytest.fixture(autouse=True)
def configured_nodes(monkeypatch):
    """Six configured nodes: the old fallback would report all six as healthy."""
    monkeypatch.setattr(
        fleet,
        "_nodes",
        {f"node-{index}": {"models": {}} for index in range(6)},
    )
    yield


def test_unreadable_health_inventory_fails_closed(monkeypatch):
    def unavailable():
        raise RuntimeError("health graph unavailable")

    monkeypatch.setattr(fleet, "node_health", unavailable)
    assert fleet.healthy_node_count() == 1


def test_non_mapping_health_inventory_fails_closed(monkeypatch):
    # A malformed health document is as unverifiable as a raised error.
    monkeypatch.setattr(fleet, "node_health", lambda: ["node-0", "node-1"])
    assert fleet.healthy_node_count() == 1


def test_missing_state_in_health_entry_fails_closed(monkeypatch):
    monkeypatch.setattr(fleet, "node_health", lambda: {"node-0": {"down": 0}})
    assert fleet.healthy_node_count() == 1


def test_non_mapping_health_entry_fails_closed(monkeypatch):
    monkeypatch.setattr(fleet, "node_health", lambda: {"node-0": "up"})
    assert fleet.healthy_node_count() == 1


def test_genuine_partial_outage_still_counts_only_up_nodes(monkeypatch):
    monkeypatch.setattr(
        fleet,
        "node_health",
        lambda: {
            "node-0": {"state": "up"},
            "node-1": {"state": "DOWN"},
            "node-2": {"state": "up"},
        },
    )
    assert fleet.healthy_node_count() == 2


def test_genuine_all_up_still_counts_every_reported_node(monkeypatch):
    monkeypatch.setattr(
        fleet,
        "node_health",
        lambda: {f"node-{index}": {"state": "up"} for index in range(4)},
    )
    assert fleet.healthy_node_count() == 4


def test_empty_health_inventory_reports_zero_not_all_configured(monkeypatch):
    monkeypatch.setattr(fleet, "node_health", lambda: {})
    assert fleet.healthy_node_count() == 0
