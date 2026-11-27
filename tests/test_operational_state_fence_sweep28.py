"""Hermetic sweep28 regression: finalize() must honour the operational fence.

No services, no Docker, no network, no real clock. Only the in-process
FakeGraph from the repo's own operational-state test and synthetic records.
"""

from __future__ import annotations

import re

import pytest

from assistx.operational_state import OperationalStateStore


def _parse_cypher_params(query: str) -> tuple[dict, str]:
    """Split a FalkorDB v4 'CYPHER k=v ...' prefix from the query body."""
    params: dict = {}
    if not query.startswith("CYPHER "):
        return params, query
    tokens = query[7:].split(" ")
    header_tokens: list[str] = []
    idx = 0
    while idx < len(tokens) and re.match(r"^[A-Za-z_]\w*=", tokens[idx]):
        header_tokens.append(tokens[idx])
        idx += 1
    body = " ".join(tokens[idx:])
    for token in header_tokens:
        key, _sep, raw = token.partition("=")
        if not _sep:
            continue
        if raw == "null":
            params[key] = None
        elif re.fullmatch(r"-?\d+", raw):
            params[key] = int(raw)
        else:
            stripped = raw[1:-1] if raw.startswith("'") and raw.endswith("'") else raw
            params[key] = stripped.replace("\\'", "'").replace("\\\\", "\\")
    return params, body


class FakeGraph:
    """Minimal FalkorDB v4 fake that enforces the fenced MERGE predicate."""

    def __init__(self):
        self.rows: dict = {}

    def execute_command(self, command, graph, query, *args):
        params, query = _parse_cypher_params(query)
        if command == "GRAPH.QUERY" and "MERGE" in query:
            existing = self.rows.get(params["record_id"])
            created = existing[7] if existing else params["now"]
            fenced = "r.expires_at_ms <= $now" in query
            if fenced and existing and existing[9] > params["now"]:
                if existing[4] != params.get("owner") or existing[5] > params["epoch"]:
                    return [[], [], []]
            row = [
                params["record_id"],
                params["kind"],
                params["logical_id"],
                params["state"],
                params.get("owner"),
                params["epoch"],
                params["payload_json"],
                created,
                params["now"],
                params["expires_at_ms"],
                params["checksum"],
            ]
            self.rows[params["record_id"]] = row
            if fenced:
                return [["r.record_id", "r.created_at_ms"], [[row[0], created]], []]
            return [["r.created_at_ms"], [[created]], []]
        if command == "GRAPH.RO_QUERY":
            row = self.rows.get(params["record_id"])
            return [[], [row] if row else [], []]
        raise AssertionError((command, query, params))


def test_stale_finalize_cannot_clobber_reacquired_lease():
    """A delayed finalizer holding an old lease must not overwrite the new owner."""
    clock = [1_000]
    store = OperationalStateStore(FakeGraph(), clock_ms=lambda: clock[0])

    stale = store.acquire_lease(
        logical_id="fleet-leader",
        owner="alpha",
        epoch=1,
        ttl_seconds=5,
    )
    assert stale.owner == "alpha" and stale.epoch == 1

    # alpha's lease lapses and beta legitimately takes over with a higher epoch.
    clock[0] = 7_000
    current = store.acquire_lease(
        logical_id="fleet-leader",
        owner="beta",
        epoch=2,
        ttl_seconds=60,
    )
    assert current.owner == "beta" and current.epoch == 2

    commits: list = []
    with pytest.raises(RuntimeError, match="fence moved"):
        store.finalize(
            stale,
            final_state="COMPLETED",
            neo4j_commit=lambda envelope: commits.append(envelope) or "durable",
        )

    # No stale durable commit, and the authoritative record is untouched.
    assert commits == []
    live = store.get("lease", "fleet-leader")
    assert live is not None
    assert live.owner == "beta"
    assert live.epoch == 2
    assert live.state == "ACTIVE"


def test_current_owner_can_finalize_lease():
    """Regression guard: the real owner's finalize still works end to end."""
    clock = [1_000]
    store = OperationalStateStore(FakeGraph(), clock_ms=lambda: clock[0])
    record = store.acquire_lease(
        logical_id="fleet-leader",
        owner="alpha",
        epoch=3,
        ttl_seconds=60,
    )
    commits: list = []
    result = store.finalize(
        record,
        final_state="COMPLETED",
        neo4j_commit=lambda envelope: commits.append(envelope) or "durable",
        evidence={"probe": "ok"},
    )
    assert result == "durable"
    assert len(commits) == 1
    live = store.get("lease", "fleet-leader")
    assert live is not None
    assert live.state == "COMPLETED"
    assert live.owner == "alpha"
    assert live.epoch == 3
    assert live.payload["durable_commit_id"] == commits[0]["commit_id"]
