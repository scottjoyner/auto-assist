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
    assert migration["reason"] == "not_paused_checkpointed_or_budget_exhausted"


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
        "reason": "task_not_running_or_not_preemptible",
    }
