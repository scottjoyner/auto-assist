"""Synthetic, read-only provenance acceptance for TraceEvent context projection."""
from unittest.mock import MagicMock

import pytest

from assistx.swarm_core import _build_trace_context, get_trace


def ev(**values):
    return {"event_type": "route.selected", "source": "auto-router", "ts_ms": 20, **values}


def test_context_multiple_top_level_ids_are_distinct_and_ordered():
    result = _build_trace_context([
        ev(task_id="task-a", dispatch_id="dispatch-1"),
        ev(task_id="task-b", route_id="route-1"),
        ev(task_id="task-a", assignment_id="assignment-1"),
    ])
    tasks = result["fields"]["task_id"]
    assert [x["value"] for x in tasks] == ["task-a", "task-b"]
    assert tasks[0] == {
        "value": "task-a", "events": 2,
        "first_event_index": 0, "last_event_index": 2,
        "provenance": "trace_event_property",
    }
    assert result["fields"]["dispatch_id"][0]["value"] == "dispatch-1"
    assert result["fields"]["assignment_id"][0]["value"] == "assignment-1"
    assert result["node_or_agent_verified"] is False
    assert result["payload_inspected"] is False


def test_only_top_level_fields_count_not_json_payload():
    result = _build_trace_context([
        ev(task_id=None, payload_json='{"task_id":"hidden","node_id":"node-x","worker_id":"w"}'),
        ev(source="other", payload_json='{"task_id":"private"}')
    ])
    assert result["fields"]["task_id"] == []
    assert "task_id" in result["missing"]
    assert {x["value"] for x in result["fields"]["source"]} == {"auto-router", "other"}
    assert "node_id" not in result["fields"]
    assert "worker_id" not in result["fields"]


def test_empty_events_are_unknown_not_inferred():
    result = _build_trace_context([])
    assert set(result["missing"]) == set(result["fields"])
    assert all(not values for values in result["fields"].values())
    assert result["source_semantics"] == "producer_label_not_verified_node_or_agent"


@pytest.mark.parametrize("value", [
    "", "\n", "legit\rrole", "x" * 161, ["node-a"], {"id": "node-a"}, 123, True
])
def test_non_scalar_or_unsafe_values_are_discarded(value):
    result = _build_trace_context([ev(task_id=value)])
    assert result["fields"]["task_id"] == []


def test_values_escape_in_ui_but_source_preserves_untrusted_text():
    text = '<img src=x onerror="alert(1)">'
    result = _build_trace_context([ev(route_id=text)])
    assert result["fields"]["route_id"][0]["value"] == text
    assert result["fields"]["route_id"][0]["provenance"] == "trace_event_property"


def test_context_field_cardinality_is_bounded():
    events = [ev(task_id="task-" + str(i)) for i in range(20)]
    result = _build_trace_context(events)
    assert len(result["fields"]["task_id"]) == 12
    assert result["truncated"] == ["task_id"]


class _Session:
    def __init__(self,rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self,*exc):
        pass

    def run(self,cypher,params):
        self.calls.append((cypher,params))
        return iter({"t": row} for row in self.rows)


class _Neo:
    def __init__(self,rows):
        self.s = _Session(rows)

    def _session(self):
        return self.s


def test_get_trace_includes_context_without_extra_graph_queries():
    neo = _Neo([
        ev(task_id="task-x", payload_json='{"node_id":"claimed-unverified"}'),
        ev(task_id="task-x", assignment_id="assign-x", event_type="assignment.claimed",
           payload_json='{"node_id":"reported-node","worker_id":"reported-worker"}'),
    ])
    result = get_trace(neo,"cid-x")
    assert result["context"]["fields"]["task_id"][0]["events"] == 2
    assert result["context"]["fields"]["assignment_id"][0]["value"] == "assign-x"
    assert result["context"]["node_or_agent_verified"] is False
    # The existing legacy summary may report payload claims, but context never
    # upgrades them to verified node/agent identity.
    assert "node_id" not in result["context"]["fields"]
    assert len(neo.s.calls) == 1
    assert neo.s.calls[0][1] == {"correlation_id": "cid-x"}
    assert "RETURN t" in neo.s.calls[0][0]


def test_get_trace_empty_is_unchanged():
    assert get_trace(_Neo([]),"missing") is None
