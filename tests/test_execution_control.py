from assistx.execution_control import ExecutionControlPlane


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def single(self):
        return self.row

    def __iter__(self):
        return iter(self.rows)


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, query, params):
        self.calls.append((query, params))
        return Result(row=self.responses.pop(0) if self.responses else None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Neo:
    def __init__(self, *responses):
        self.session = Session(responses)

    def _session(self):
        return self.session


def test_checkpoint_records_bounded_progress_and_pause():
    neo = Neo(
        {
            "t": {
                "id": "task-1",
                "status": "PAUSED",
                "checkpoint_json": '{"cursor":4}',
            },
            "checkpoint": {
                "id": "checkpoint-1",
                "checkpoint_json": '{"cursor":4}',
            },
        }
    )

    result = ExecutionControlPlane().checkpoint(
        neo,
        "task-1",
        "node-a",
        "claim-1",
        checkpoint={"cursor": 4},
        progress=2.0,
        estimated_remaining_seconds=30,
        pause=True,
    )

    assert result["checkpointed"] is True
    assert result["task"]["checkpoint"]["cursor"] == 4
    assert result["checkpoint"]["checkpoint"]["cursor"] == 4
    assert neo.session.calls[0][1]["progress"] == 1.0


def test_stale_checkpoint_and_invalid_migration_are_blocked():
    checkpoint = ExecutionControlPlane().checkpoint(
        Neo(None),
        "task-1",
        "old-node",
        "old-claim",
        checkpoint={},
        progress=0,
        estimated_remaining_seconds=None,
        pause=True,
    )
    migration = ExecutionControlPlane().migrate(
        Neo(None),
        "task-1",
        "node-b",
        "operator",
    )

    assert checkpoint["reason"] == "stale_or_non_owner_execution"
    assert migration["reason"] == "migration_ineligible_target_checkpoint_or_budget"


def test_checkpoint_size_is_bounded_before_database_write(monkeypatch):
    monkeypatch.setenv("ASSISTX_MAX_CHECKPOINT_BYTES", "1024")
    neo = Neo()

    result = ExecutionControlPlane().checkpoint(
        neo,
        "task-1",
        "node-a",
        "claim-1",
        checkpoint={"payload": "x" * 2048},
        progress=0.5,
        estimated_remaining_seconds=None,
        pause=True,
    )

    assert result["checkpointed"] is False
    assert result["reason"] == "checkpoint_too_large"
    assert result["checkpoint_bytes"] > result["max_checkpoint_bytes"]
    assert neo.session.calls == []


def test_migration_requires_compatible_target_and_supersedes_reservation():
    neo = Neo({"t": {"id": "task-1", "status": "READY"}})

    result = ExecutionControlPlane().migrate(
        neo,
        "task-1",
        "node-b",
        "operator",
    )

    query, params = neo.session.calls[0]
    assert result["migrated"] is True
    assert "required IN coalesce(target.capabilities, [])" in query
    assert "reservation.status" in query
    assert "['ACTIVE','CLAIMED']" in query
    assert "'SUPERSEDED'" in query
    assert params["target_agent_id"] == "node-b"


def test_preemption_requires_running_preemptible_task():
    result = ExecutionControlPlane().request_preemption(
        Neo(None),
        "task-1",
        "operator",
        reason="interactive capacity needed",
        target_agent_id="node-b",
    )

    assert result == {
        "requested": False,
        "reason": "task_not_preemptible_running_or_target_ineligible",
    }


def test_claim_endpoint_does_not_forward_an_unsupported_claim_token(monkeypatch):
    from assistx import api

    class ClaimNeo:
        def __init__(self):
            self.kwargs = {}

        def get_task(self, _task_id):
            return {"id": "task-1", "status": "READY", "kind": "analysis"}

        def claim_task(self, **kwargs):
            self.kwargs = kwargs
            return {"claimed": True, "task": {"id": "task-1", "status": "CLAIMED"}}

        @staticmethod
        def _with_retry(operation):
            return operation()

    neo = ClaimNeo()
    monkeypatch.setattr(api, "_neo_fleet", lambda: neo)
    monkeypatch.setattr(
        api,
        "_is_claim_allowed_for_workflow_control",
        lambda _task: (True, ""),
    )

    result = api.api_claim_task(
        "task-1",
        api.TaskClaimIn(agent_id="node-a", capabilities=["llm"]),
        None,
        "operator",
    )

    assert result["claimed"] is True
    assert neo.kwargs["agent_id"] == "node-a"
    assert "claim_id" not in neo.kwargs
