"""Proof that numeric Neo4j task priorities do not break production executor queries."""

from assistx.fleet_executor import FleetExecutor


class NumericPrioritySession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, parameters):
        # Neo4j rejects toLower(integer). Require the conversion in the query
        # before representing a live mixed-priority result set.
        assert "toLower(toString(coalesce(t.priority, 'background')))" in query
        assert parameters["limit"] == 20
        return [
            {"t": {"id": "urgent", "priority": 100, "status": "READY"}},
            {"t": {"id": "batch", "priority": "BACKGROUND", "status": "READY"}},
        ]


class NumericPriorityGraph:
    def _session(self):
        return NumericPrioritySession()


def test_list_ready_handles_numeric_and_string_priorities():
    executor = FleetExecutor(
        neo_factory=lambda: None,
        projection_loader=lambda: {},
    )
    try:
        tasks = executor._list_ready_llm_tasks(NumericPriorityGraph(), 20)
        assert [row["id"] for row in tasks] == ["urgent", "batch"]
    finally:
        executor.stop()
