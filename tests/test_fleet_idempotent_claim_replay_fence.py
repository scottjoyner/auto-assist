"""Synthetic Neo4j replay-custody tests: no live queries, leases, or writes."""
from contextlib import contextmanager
import time

import pytest

from assistx.neo4j_client import Neo4jClient


class Row:
    def __init__(self, data):
        self.data = data

    def single(self):
        return self.data


class FakeSession:
    def __init__(self, existing):
        self.existing = dict(existing)
        self.queries = []

    def run(self, query, args):
        self.queries.append((query, dict(args)))
        if "MATCH (t:Task {id:$task_id, claim_id:$claim_id})" in query:
            # Emulates Neo4j conditional matching. Every intended predicate
            # must be present in the Cypher itself, not just in this fixture.
            t = self.existing
            required_fragments = (
                "t.status='CLAIMED'",
                "t.claimed_by=$agent_id",
                "t.agent_session_id=$session_id",
                "t.lease_expires_at_ts > timestamp()",
                "(t.target_agent_id IS NULL OR t.target_agent_id=$agent_id)",
                "required IN $capabilities",
                "NOT EXISTS",
            )
            if not all(fragment in query for fragment in required_fragments):
                return Row({"t": t})  # Exposes the old unguarded replay.
            valid = (
                t.get("id") == args["task_id"]
                and t.get("claim_id") == args["claim_id"]
                and t.get("status") == "CLAIMED"
                and t.get("claimed_by") == args.get("agent_id")
                and t.get("agent_session_id") == args.get("session_id")
                and bool(args.get("session_id"))
                and type(t.get("lease_expires_at_ts")) is int
                and t["lease_expires_at_ts"] > int(time.time() * 1000)
                and t.get("target_agent_id") in (None, args.get("agent_id"))
                and all(cap in args.get("capabilities", []) for cap in
                        (t.get("required_capabilities") or t.get("capabilities") or []))
                and not t.get("paperclip_dispatched", False)
            )
            return Row({"t": t} if valid else None)
        if "WHERE t.status='READY'" in query:
            return Row(None)
        if "RETURN t.status AS status" in query:
            t = self.existing
            return Row({
                "status": t.get("status"),
                "target_agent_id": t.get("target_agent_id"),
                "capabilities": t.get("capabilities"),
                "required_capabilities": t.get("required_capabilities"),
                "paperclip_dispatched": t.get("paperclip_dispatched", False),
            })
        raise AssertionError("Unexpected graph operation")


def replay(task, *, agent="node-a", session="session-a", caps=None):
    session_stub = FakeSession(task)
    client = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def synthetic_session():
        yield session_stub

    client._session = synthetic_session
    result = client.claim_task(
        task_id="task-1", agent_id=agent,
        capabilities=["llm"] if caps is None else caps,
        session_id=session, idempotency_key="replay-fence",
    )
    return result, session_stub.queries


def active_claim():
    return {
        "id": "task-1",
        "claim_id": "replay-fence",
        "status": "CLAIMED",
        "claimed_by": "node-a",
        "agent_session_id": "session-a",
        "target_agent_id": "node-a",
        "required_capabilities": ["llm"],
        "lease_expires_at_ts": int(time.time() * 1000) + 300_000,
        "paperclip_dispatched": False,
    }


def test_exact_live_owner_session_fence_and_unexpired_lease_replays():
    result, queries = replay(active_claim())
    assert result["claimed"] is True
    assert result["idempotent"] is True
    assert result["claim_id"] == "replay-fence"
    assert result["task"]["id"] == "task-1"
    assert len(queries) == 1  # No CREATE, SET or other mutation


@pytest.mark.parametrize("changed", [
    {"claimed_by": "node-b"},
    {"agent_session_id": "old-session"},
    {"agent_session_id": None},
    {"status": "DONE"},
    {"status": "READY"},
    {"status": "RUNNING"},
    {"lease_expires_at_ts": 0},
    {"lease_expires_at_ts": None},
    {"target_agent_id": "node-b"},
    {"required_capabilities": ["gpu"]},
    {"paperclip_dispatched": True},
])
def test_stale_cross_owner_or_revoked_replay_is_never_claimed(changed):
    task = {**active_claim(), **changed}
    result, queries = replay(task)
    assert result["claimed"] is False
    assert result["reason"] in {"not_ready", "not_claimed", "capability_mismatch",
                                "target_agent_mismatch", "paperclip_dispatched"}
    # The guarded idempotent lookup is read-only; later conditional READY
    # CAS may be attempted, but the synthetic existing Task is not READY.
    assert "SET t.status='CLAIMED'" not in queries[0][0]


@pytest.mark.parametrize("agent,session,caps", [
    ("node-b", "session-a", ["llm"]),
    ("node-a", "forged-session", ["llm"]),
    ("node-a", None, ["llm"]),
    ("node-a", "session-a", []),
    ("node-a", "session-a", ["script"]),
])
def test_wrong_caller_identity_or_capability_cannot_replay(agent, session, caps):
    result, _ = replay(active_claim(), agent=agent, session=session, caps=caps)
    assert result["claimed"] is False


def test_stale_idempotency_key_never_reclaims_a_reset_ready_task():
    task = {**active_claim(), "status": "READY"}
    result, queries = replay(task)
    assert result["claimed"] is False
    cas = next(q for q, _ in queries if "WHERE t.status='READY'" in q)
    # Reset READY tasks can be claimed with fresh keys under normal policy,
    # but not the same stale claim identifier after replay validation failed.
    assert "t.claim_id IS NULL OR t.claim_id <> $claim_id" in cas


def test_replay_lookup_has_complete_graph_predicates():
    result, queries = replay(active_claim())
    assert result["claimed"] is True
    assert len(queries) == 1
    query, params = queries[0]
    for term in (
        "t.status='CLAIMED'",
        "t.claimed_by=$agent_id",
        "t.agent_session_id=$session_id",
        "t.lease_expires_at_ts > timestamp()",
        "(t.target_agent_id IS NULL OR t.target_agent_id=$agent_id)",
        "required IN $capabilities",
        "NOT EXISTS",
    ):
        assert term in query
    assert params == {
        "task_id": "task-1", "claim_id": "replay-fence",
        "agent_id": "node-a", "session_id": "session-a",
        "capabilities": ["llm"],
    }


class FakeFreshClaimSession:
    """Emulate a conditional READY CAS without connecting to a graph."""
    def __init__(self, required):
        self.required = required
        self.calls = []

    def run(self, query, args):
        self.calls.append((query, dict(args)))
        if "WHERE t.status='READY'" in query:
            # The old OR size($capabilities)=0 treats missing capacity as
            # sufficient for an arbitrary required task.
            cap_match = ("size($capabilities)=0" in query
                         and not args["capabilities"])
            cap_match = cap_match or all(
                cap in args["capabilities"] for cap in self.required)
            return Row({"t": {
                "id": args["task_id"],
                "status": "CLAIMED",
                "claim_id": args["claim_id"],
                "claimed_by": args["agent_id"],
                "agent_session_id": args["session_id"],
                "lease_expires_at_ts": int(time.time() * 1000) + 900000,
            }} if cap_match else None)
        if "RETURN t.status AS status" in query:
            return Row({
                "status": "READY",
                "target_agent_id": None,
                "required_capabilities": self.required,
                "capabilities": None,
                "paperclip_dispatched": False,
            })
        raise AssertionError("Unexpected graph operation")


@pytest.mark.parametrize("caps,required,allowed", [
    ([], ["llm"], False),
    ([], ["gpu"], False),
    ([], [], True),
    (["llm"], ["llm"], True),
    (["llm"], ["gpu"], False),
    (["llm", "gpu"], ["llm", "gpu"], True),
])
def test_new_claim_without_required_capabilities_must_not_escalate(
    caps, required, allowed
):
    store = FakeFreshClaimSession(required)
    client = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def session():
        yield store

    client._session = session
    result = client.claim_task("task-new", "node-a", caps, session_id=None)
    assert result["claimed"] is allowed
    if not allowed:
        assert result["reason"] == "capability_mismatch"
