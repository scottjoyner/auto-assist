"""In-memory fake of ``assistx.neo4j_client.Neo4jClient`` for tests.

It records the Cypher statements handed to ``session.run`` so trace-linkage
tests can assert that the expected ``:TraceEvent`` / ``:TraceGroup`` nodes and
``FOR_*`` relationships are created — without standing up a real Neo4j.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class CapturingSession:
    """Stands in for a ``neo4j.Session``; stores every run() call."""

    def __init__(self, store: "FakeNeo4j") -> None:
        self._store = store

    def run(self, cypher: str, parameters: Optional[Dict[str, Any]] = None) -> "CapturingResult":
        self._store.record(cypher, parameters or {})
        return CapturingResult(self._store, cypher, parameters or {})

    def close(self) -> None:  # pragma: no cover - interface parity
        return None

    def __enter__(self) -> "CapturingSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class CapturingResult:
    """Minimal result object: supports .single(), .data(), iteration."""

    def __init__(self, store: "FakeNeo4j", cypher: str, params: Dict[str, Any]) -> None:
        self._store = store
        self._cypher = cypher
        self._params = params

    def single(self) -> Optional[Dict[str, Any]]:
        # dbms.procedures() probe for APOC availability.
        if "dbms.procedures" in self._cypher:
            return {"c": 1 if self._store.apoc else 0}
        # Generic single-row fetch returns None (no pre-existing nodes).
        return None

    def data(self) -> List[Dict[str, Any]]:
        return []

    def __iter__(self):
        # For the inline TraceGroup state recompute, return the recorded
        # event_types so _update_trace_state_inline derives a state.
        if "RETURN t.event_type" in self._cypher:
            # The real query orders by ts_ms DESC and takes the latest; emulate
            # that by returning the most-recently-recorded type first.
            return iter({"et": et} for et in reversed(self._store._trace_event_types))
        return iter([])

    def consume(self) -> None:  # pragma: no cover - interface parity
        return None


class FakeNeo4j:
    """Fake store exposing the subset of ``Neo4jClient`` used by swarm_core."""

    def __init__(self, apoc: bool = True) -> None:
        self.apoc = apoc
        self.runs: List[Dict[str, Any]] = []
        self._trace_event_types: List[str] = []

    def record(self, cypher: str, params: Dict[str, Any]) -> None:
        # Capture the event_type whenever a TraceEvent node is created so that
        # the inline TraceGroup state recompute (which queries event_type rows)
        # has something to return.
        if "TraceEvent" in cypher and "event_type=$event_type" in cypher:
            et = params.get("event_type")
            if et:
                self._trace_event_types.append(et)
        self.runs.append({"cypher": cypher, "params": params})

    def _session(self) -> CapturingSession:
        return CapturingSession(self)

    # --- assertion helpers -------------------------------------------------
    def _statements(self) -> List[str]:
        return [r["cypher"] for r in self.runs]

    def has_node(self, label: str, key: str, value: Optional[str] = None) -> bool:
        for r in self.runs:
            cypher = r["cypher"]
            # Matches ``(:TraceGroup ...)`` or ``(g:TraceGroup ...)``.
            if f":{label}" not in cypher and f"({label}" not in cypher:
                continue
            if value is None:
                return True
            # The key is supplied as a parameter; ensure it appears in this
            # statement's params (e.g. correlation_id / event_id / id).
            if key in r["params"] and r["params"][key] == value:
                return True
        return False

    def has_relationship(
        self,
        rel: str,
        from_label: Optional[str] = None,
        to_label: Optional[str] = None,
        to_id: Optional[str] = None,
    ) -> bool:
        for r in self.runs:
            cypher = r["cypher"]
            # Relationship creation uses ``MERGE (t)-[:FOR_TASK]->(n:Task)``.
            if f"[:{rel}]" not in cypher and f"-[:{rel}]->" not in cypher and f"-[{rel}]->" not in cypher:
                continue
            if to_label and f":{to_label}" not in cypher and f"({to_label}" not in cypher:
                continue
            if to_id is None or r["params"].get("target_id") == to_id:
                return True
        return False

    def get_property(
        self, label: str, key: str, value: str, prop: str
    ) -> Optional[Any]:
        # Return the LAST current_state SET (latest event wins).
        for r in reversed(self.runs):
            if "current_state" in r["cypher"] and f":{label}" in r["cypher"]:
                return r["params"].get("state")
        return None
