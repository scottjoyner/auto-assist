import importlib.util
import sys
import unittest
from pathlib import Path

scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("trace_catalog_query", scripts / "trace_catalog_query.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def record(number, node="x1-370", session="session-1", trace="trace-1"):
    return {
        "schema": "assistx.fleet.trace.v1",
        "event_id": "event-" + str(number),
        "span_id": "span-" + str(number),
        "trace_id": trace,
        "session_id": session,
        "node_id": node,
        "producer": "assistx",
        "operation": "tool",
        "status": "ok",
        "sequence": number,
        "occurred_at": f"2026-10-10T16:00:{number:02d}Z",
    }


class TraceCatalogQueryTests(unittest.TestCase):
    def test_filters_and_keyset_pages_are_stable(self):
        rows = [record(3, "xwing"), record(2), record(4, "xwing"), record(1)]
        first = catalog.search_metadata(rows, filters={"node_id": "xwing"}, limit=1)
        self.assertEqual([x["event_id"] for x in first["items"]], ["event-3"])
        self.assertEqual(first["matched_after_cursor"], 2)
        second = catalog.search_metadata(rows, filters={"node_id": "xwing"},
                                         cursor=first["next_cursor"], limit=1)
        self.assertEqual([x["event_id"] for x in second["items"]], ["event-4"])
        self.assertIsNone(second["next_cursor"])
        self.assertFalse(second["persistent_index_created"])

    def test_duplicate_same_event_is_idempotent(self):
        rows = [record(1), record(1)]
        self.assertEqual(len(catalog.search_metadata(rows)["items"]), 1)

    def test_conflicting_events_and_raw_payloads_denied(self):
        changed = dict(record(1), node_id="xwing")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            catalog.search_metadata([record(1), changed])
        with self.assertRaisesRegex(ValueError, "unapproved"):
            catalog.search_metadata([dict(record(1), raw_prompt="private")])

    def test_budget_cursor_and_unknown_query_fields_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            catalog.search_metadata([record(1), record(2)], maximum_rows=1)
        with self.assertRaisesRegex(ValueError, "cursor"):
            catalog.search_metadata([record(1)], cursor="not-a-cursor")
        with self.assertRaisesRegex(ValueError, "unsupported query"):
            catalog.search_metadata([record(1)], filters={"gps": "anything"})

    def test_no_raw_data_in_results(self):
        query = catalog.search_metadata([record(1)], filters={"trace_id": "trace-1"})
        self.assertEqual(set(query["items"][0]), set(record(1)))
        self.assertFalse(query["complete_fleet_coverage"])


if __name__ == "__main__":
    unittest.main()
