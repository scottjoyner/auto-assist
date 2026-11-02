"""Initial and idempotent claim responses must carry authoritative fence IDs."""
from contextlib import contextmanager

from assistx.neo4j_client import Neo4jClient


class Rows:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, query, args):
        self.calls.append((query, args))
        if "WHERE t.status='READY'" in query:
            return Rows({"t": {
                "id": args["task_id"], "status": "CLAIMED",
                "claimed_by": args["agent_id"],
                "agent_session_id": args["session_id"],
                "claim_id": args["claim_id"],
            }})
        return Rows(None)


def test_first_claim_returns_fence_at_top_level_and_inside_task(monkeypatch):
    session = FakeSession()
    client = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(client, "_session", fake_session)
    result = client.claim_task("task-1", "node-a", ["llm"], "session-a")
    assert result["claimed"] is True
    assert len(result["claim_id"]) == 32
    assert result["claim_id"] == result["task"]["claim_id"]
    assert result["task"]["agent_session_id"] == "session-a"
    assert len(session.calls) == 2
