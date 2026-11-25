"""Synthetic stale lease, session and idempotent-completion mutation checks.

No Neo4j socket, Task graph, credentials, or model requests.
"""
from contextlib import contextmanager
import time

import pytest
from assistx.neo4j_client import Neo4jClient


def current_task(**changes):
    return {
        "id": "task-1", "status": "CLAIMED", "claimed_by": "node-a",
        "agent_session_id": "session-a", "claim_id": "claim-current",
        "execution_attempt": 1,
        "lease_expires_at_ts": int(time.time() * 1000) + 60_000,
        "completed_by": None, "result_json": "{}", "result_summary": None,
        **changes,
    }


class Row:
    def __init__(self, value):
        self.value = value

    def single(self):
        return self.value


class Fake:
    def __init__(self, task, prior_run=None):
        self.task = task
        self.prior_run = prior_run
        self.queries = []
        self.writes = []

    def _claimed_valid(self, args):
        return (
            self.task["status"] in {"CLAIMED", "RUNNING", "PAUSING"}
            and self.task["claimed_by"] == args["agent_id"]
            and self.task["claim_id"] == args["claim_id"]
            and self.task.get("agent_session_id") == (
                args.get("session_id") or self.task.get("agent_session_id")
            )
            and self.task.get("lease_expires_at_ts", 0) > int(time.time() * 1000)
        )

    def run(self, query, args=None):
        args = dict(args or {})
        self.queries.append((query, args))
        if "EXECUTED_BY]" in query and "AgentRun {completion_id:$completion_id}" in query and "CREATE (r:AgentRun" not in query:
            if self.prior_run is None:
                return Row(None)
            run = self.prior_run
            guarded = (
                "t.completed_by=$agent_id" in query
                and "r.agent=$agent_id" in query
                and "r.status=$status" in query
                and "t.status=$status" in query
                and "r.claim_id=$claim_id" in query
            )
            allowed = (not guarded or (
                self.task.get("status") == args["status"]
                and self.task.get("completed_by") == args["agent_id"]
                and run.get("agent") == args["agent_id"]
                and run.get("status") == args["status"]
                and run.get("claim_id") == args["claim_id"]
                and self.task.get("claim_id") == args["claim_id"]
                and self.task.get("agent_session_id") == args.get("session_id")
            ))
            return Row({"t": self.task, "run_id": run["id"],
                        "run_summary": run.get("summary"),
                        "run_result_json": run.get("result_json", "{}")} if allowed else None)
        if "SET t += $props" in query:
            guarded = (
                "t.lease_expires_at_ts > timestamp()" in query
                and "t.claim_id=$claim_id" in query
                and "t.agent_session_id=$session_id" in query
            )
            permitted = not guarded or self._claimed_valid(args)
            if permitted:
                self.writes.append("heartbeat")
                return Row({"t": self.task})
            return Row(None)
        if "CREATE (r:AgentRun" in query:
            if (self.prior_run and args.get("completion_id") and
                "NOT EXISTS {" in query and
                "(:AgentRun {completion_id:$completion_id})" in query):
                return Row(None)
            guarded = (
                "t.lease_expires_at_ts > timestamp()" in query
                and "t.claim_id=$claim_id" in query
                and "t.agent_session_id=$session_id" in query
                and "claim_id:$claim_id" in query
            )
            permitted = not guarded or self._claimed_valid(args)
            if permitted:
                self.writes.append("completion")
                return Row({"t": {**self.task, "status": args["status"]},
                            "run_id": args["run_id"]})
            return Row(None)
        if "SET t.status=$status" in query:
            # Legacy idempotent replay's unsafe re-write path.
            self.writes.append("idempotent_rewrite")
            return Row(None)
        raise AssertionError("unexpected mock query")


def client(task, prior_run=None):
    store = Fake(task, prior_run)
    neo = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def session():
        yield store

    neo._session = session
    return neo, store


@pytest.mark.parametrize("change", [
    {"claimed_by": "node-b"},
    {"agent_session_id": "other-session"},
    {"claim_id": "old-claim"},
    {"status": "DONE"},
    {"lease_expires_at_ts": 0},
])
def test_stale_heartbeat_does_not_extend_lease(change):
    neo, store = client(current_task(**change))
    result = neo.heartbeat_task(
        "task-1", "node-a", status="RUNNING", session_id="session-a",
        claim_id="claim-current",
    )
    assert result is None
    assert store.writes == []


def test_valid_heartbeat_respects_claim_and_preserves_session():
    neo, store = client(current_task())
    result = neo.heartbeat_task(
        "task-1", "node-a", status="RUNNING", session_id="session-a",
        claim_id="claim-current",
    )
    assert result is not None
    assert store.writes == ["heartbeat"]


@pytest.mark.parametrize("change", [
    {"claimed_by": "node-b"},
    {"agent_session_id": "different"},
    {"claim_id": "wrong"},
    {"status": "DONE"},
    {"lease_expires_at_ts": 0},
])
def test_stale_completion_never_writes_new_run(change):
    neo, store = client(current_task(**change))
    result = neo.complete_task(
        "task-1", "node-a", "DONE", session_id="session-a",
        claim_id="claim-current",
    )
    assert result is None
    assert store.writes == []


def test_current_claim_completes_with_claim_fence_recorded_on_run():
    neo, store = client(current_task())
    result = neo.complete_task(
        "task-1", "node-a", "DONE", session_id="session-a",
        claim_id="claim-current",
    )
    assert result["status"] == "DONE"
    assert store.writes == ["completion"]
    q = store.queries[0][0]
    assert "claim_id:$claim_id" in q


@pytest.mark.parametrize("changed", [
    {"agent": "node-b"},
    {"status": "FAILED"},
    {"claim_id": "other-claim"},
])
def test_prior_completion_must_match_exact_original_run(changed):
    run = {"id": "run-1", "agent": "node-a",
           "status": "DONE", "claim_id": "claim-current", **changed}
    task = current_task(status="DONE", completed_by="node-a")
    neo, store = client(task, prior_run=run)
    result = neo.complete_task(
        "task-1", "node-a", "DONE", idempotency_key="complete-once",
        session_id="session-a", claim_id="claim-current",
    )
    assert result is None
    assert store.writes == []


def test_idempotent_replay_cannot_rewrite_terminal_result():
    task = current_task(status="FAILED", completed_by="node-a")
    run = {"id": "run-1", "agent": "node-a", "status": "FAILED",
           "claim_id": "claim-current"}
    neo, store = client(task, prior_run=run)
    result = neo.complete_task(
        "task-1", "node-a", "DONE", idempotency_key="complete-once",
        session_id="session-a", claim_id="claim-current",
    )
    assert result is None
    assert store.writes == []


def test_identical_terminal_retry_is_read_only():
    task = current_task(status="DONE", completed_by="node-a")
    run = {"id": "run-1", "agent": "node-a", "status": "DONE",
           "claim_id": "claim-current"}
    neo, store = client(task, prior_run=run)
    result = neo.complete_task(
        "task-1", "node-a", "DONE", idempotency_key="complete-once",
        session_id="session-a", claim_id="claim-current",
    )
    assert result["idempotent"] is True
    assert result["run_id"] == "run-1"
    assert store.writes == []


@pytest.mark.parametrize("terminal", ["DONE", "FAILED", "CANCELLED"])
def test_heartbeat_must_not_mark_task_terminal_without_completion(terminal):
    neo, store = client(current_task())
    with pytest.raises(ValueError, match="Terminal task status requires"):
        neo.heartbeat_task(
            "task-1", "node-a", status=terminal,
            session_id="session-a", claim_id="claim-current",
        )
    assert store.queries == []
    assert store.writes == []


@pytest.mark.parametrize("result,summary", [({"ok": False}, "original"), ({"ok": True}, "changed")])
def test_completion_replay_rejects_changed_result_or_summary(result, summary):
    task = current_task(status="DONE", completed_by="node-a", result_json='{"ok":true}', result_summary="original")
    run = {"id":"run-1", "agent":"node-a", "status":"DONE", "claim_id":"claim-current", "result_json":'{"ok":true}', "summary":"original"}
    neo, store = client(task, prior_run=run)
    assert neo.complete_task("task-1", "node-a", "DONE", result=result, summary=summary, idempotency_key="complete-once", session_id="session-a", claim_id="claim-current") is None
    assert store.writes == []
    assert len(store.queries) == 1


def test_completion_replay_accepts_equivalent_json_key_order():
    task = current_task(status="DONE", completed_by="node-a", result_json='{"z":1,"a":2}')
    run = {"id":"run-1", "agent":"node-a", "status":"DONE", "claim_id":"claim-current", "result_json":'{"a":2,"z":1}'}
    neo, store = client(task, prior_run=run)
    assert neo.complete_task("task-1", "node-a", "DONE", result={"z":1,"a":2}, idempotency_key="complete-once", session_id="session-a", claim_id="claim-current")["idempotent"] is True
    assert not store.writes


def test_conflicting_existing_completion_key_cannot_create_new_run():
    task = current_task()
    run = {"id":"run-1", "agent":"node-b", "status":"FAILED", "claim_id":"old-claim"}
    neo, store = client(task, prior_run=run)
    assert neo.complete_task("task-1", "node-a", "DONE", idempotency_key="complete-once", session_id="session-a", claim_id="claim-current") is None
    assert not store.writes
    assert "NOT EXISTS {" in store.queries[-1][0]
