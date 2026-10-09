"""Synthetic tests for the bounded metadata-only live trace watcher."""

import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "live_trace_watch", HERE / "scripts" / "live_trace_watch.py"
)
assert SPEC and SPEC.loader
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)


def event(i, ts=None, **extra):
    row = {
        "event_id": f"evt-{i:04d}",
        "ts_ms": ts if ts is not None else i * 1000,
        "event_type": "task.progress",
        "source": "synthetic",
        "task_id": f"task-{i}",
        "dispatch_id": None,
        "route_id": None,
        "assignment_id": None,
    }
    row.update(extra)
    return row
def page(events, *, more=False, cursor=None):
    return {
        "schema": "trace-event-page-v1",
        "correlation_id": "cid-one",
        "events": events,
        "returned": len(events),
        "page_size_max": 100,
        "has_more": more,
        "next_cursor": cursor,
        "metadata_only": True,
    }


def test_bootstrap_reads_only_one_page_and_emits_nothing_by_default():
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return page([event(3), event(2)], more=True, cursor="older")

    state = watch.TailState("cid-one", 100)
    result = watch.collect_poll(fetch, state, max_pages=4)
    assert result["events"] == []
    assert result["pages"] == 1
    assert calls == [None]
    assert "evt-0003" in state.seen
def test_burst_catches_up_across_pages_until_known_history():
    state = watch.TailState("cid-one", 100)
    watch.collect_poll(
        lambda _cursor: page([event(3), event(2)]),
        state,
        max_pages=4,
    )
    pages = {
        None: page([event(7), event(6)], more=True, cursor="p2"),
        "p2": page([event(5), event(4)], more=True, cursor="p3"),
        "p3": page([event(3), event(2)], more=False),
    }
    result = watch.collect_poll(lambda cursor: pages[cursor], state, max_pages=4)
    assert [row["event_id"] for row in result["events"]] == [
        "evt-0004", "evt-0005", "evt-0006", "evt-0007"
    ]
    assert result["pages"] == 3
    assert result["reached_known"] is True
    assert result["gap_possible"] is False


def test_page_budget_exhaustion_is_explicit_gap_not_false_completeness():
    state = watch.TailState(
        "cid-one", 100, seen=["evt-0001"], bootstrapped=True
    )
    pages = {
        None: page([event(6), event(5)], more=True, cursor="p2"),
        "p2": page([event(4), event(3)], more=True, cursor="p3"),
    }
    result = watch.collect_poll(lambda cursor: pages[cursor], state, max_pages=2)
    assert result["gap_possible"] is True
    assert result["reached_known"] is False
    assert [row["event_id"] for row in result["events"]] == [
        "evt-0003", "evt-0004", "evt-0005", "evt-0006"
    ]


def test_payload_shaped_fields_are_dropped_and_duplicate_ids_emit_once():
    state = watch.TailState(
        "cid-one", 100, seen=["evt-0001"], bootstrapped=True
    )
    bad = event(
        2,
        payload_json="DO_NOT_LEAK",
        payload_preview="DO_NOT_LEAK_EITHER",
    )
    result = watch.collect_poll(
        lambda _cursor: page([bad, dict(bad), event(1)]),
        state,
        max_pages=1,
    )
    assert len(result["events"]) == 1
    row = result["events"][0]
    assert row["event_id"] == "evt-0002"
    assert "payload_json" not in row
    assert "payload_preview" not in row
    assert "DO_NOT_LEAK" not in json.dumps(row)


def test_optional_state_file_contains_identifiers_only():
    state = watch.TailState(
        "cid-one",
        4,
        seen=["evt-0001", "evt-0002"],
        latest_ts_ms=2000,
        bootstrapped=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "watch-state.json"
        watch.save_state(path, state)
        raw = path.read_text()
        stored = json.loads(raw)
        assert set(stored) == {
            "schema", "correlation_id", "bootstrapped",
            "latest_ts_ms", "seen_event_ids"
        }
        assert "payload" not in raw.lower()
        loaded = watch.load_state(path, "cid-one", 4)
        assert loaded.bootstrapped is True
        assert loaded.latest_ts_ms == 2000
        assert loaded.seen.as_list() == ["evt-0001", "evt-0002"]


def test_emit_existing_is_explicit_and_still_bounded_to_bootstrap_page():
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return page([event(3), event(2)], more=True, cursor="older")

    state = watch.TailState("cid-one", 100)
    result = watch.collect_poll(
        fetch, state, max_pages=10, emit_existing=True
    )
    assert [row["event_id"] for row in result["events"]] == [
        "evt-0002", "evt-0003"
    ]
    assert calls == [None]