from assistx.my_jev_trajectory import enrich_shadow_trajectory


def test_trajectory_flattens_explicit_execution_evidence():
    row = {
        "intent_id": "intent-1",
        "created_tasks": [
            {
                "id": "task-1",
                "status": "DONE",
                "approved_by": "operator",
                "approved_at_ts": 100,
                "completed_by": "agent-a",
                "completed_at_ts": 200,
                "result_summary": "completed",
                "result_json": (
                    '{"status":"verified",'
                    '"verification":{"checks":["ok"]}}'
                ),
                "agent_runs": [
                    {
                        "id": "run-1",
                        "agent": "agent-a",
                        "model": "model-a",
                        "status": "DONE",
                        "summary": "completed",
                        "result_json": '{"ok":true}',
                        "started_at_ts": 120,
                        "ended_at_ts": 190,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "tool": "acceptance",
                                "ok": True,
                                "input_json": '{"suite":"smoke"}',
                                "output_json": (
                                    '{"passed":true,'
                                    '"checks":["smoke"]}'
                                ),
                                "started_at_ts": 170,
                                "ended_at_ts": 180,
                            }
                        ],
                    }
                ],
            }
        ],
    }

    enriched = enrich_shadow_trajectory(row)
    trajectory = enriched["trajectory"]

    assert trajectory["approvals"] == [
        {
            "task_id": "task-1",
            "approved_by": "operator",
            "approved_at_ts": 100,
        }
    ]
    assert trajectory["task_outcomes"][0]["status"] == "DONE"
    assert trajectory["task_outcomes"][0]["result"]["status"] == "verified"
    assert trajectory["agent_runs"][0]["result"] == {"ok": True}
    assert trajectory["tool_calls"][0]["input"] == {
        "suite": "smoke"
    }
    assert trajectory["tool_calls"][0]["output"]["passed"] is True
    assert {
        item["source"]
        for item in trajectory["verifications"]
    } == {
        "task_result",
        "acceptance_tool",
    }


def test_trajectory_does_not_infer_approval_from_ready_or_done_status():
    row = {
        "created_tasks": [
            {
                "id": "task-ready",
                "status": "READY",
                "agent_runs": [],
            },
            {
                "id": "task-done",
                "status": "DONE",
                "completed_by": "agent-a",
                "agent_runs": [],
            },
        ]
    }

    trajectory = enrich_shadow_trajectory(row)["trajectory"]

    assert trajectory["approvals"] == []
    assert [
        item["task_id"]
        for item in trajectory["task_outcomes"]
    ] == ["task-done"]


def test_trajectory_does_not_infer_verification_from_successful_task():
    row = {
        "created_tasks": [
            {
                "id": "task-1",
                "status": "DONE",
                "result_json": '{"ok":true}',
                "agent_runs": [
                    {
                        "id": "run-1",
                        "status": "DONE",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "tool": "shell",
                                "ok": True,
                                "output_json": '{"exit_code":0}',
                            }
                        ],
                    }
                ],
            }
        ]
    }

    trajectory = enrich_shadow_trajectory(row)["trajectory"]

    assert trajectory["verifications"] == []


def test_trajectory_preserves_unparseable_json_as_raw_text():
    row = {
        "created_tasks": [
            {
                "id": "task-1",
                "status": "FAILED",
                "result_json": "not-json",
                "agent_runs": [
                    {
                        "id": "run-1",
                        "result_json": "{broken",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "tool": "shell",
                                "input_json": "raw-input",
                                "output_json": "raw-output",
                            }
                        ],
                    }
                ],
            }
        ]
    }

    trajectory = enrich_shadow_trajectory(row)["trajectory"]

    assert trajectory["task_outcomes"][0]["result"] == "not-json"
    assert trajectory["agent_runs"][0]["result"] == "{broken"
    assert trajectory["tool_calls"][0]["input"] == "raw-input"
    assert trajectory["tool_calls"][0]["output"] == "raw-output"


def test_trajectory_handles_missing_or_non_list_tasks():
    row = {"created_tasks": None}

    enriched = enrich_shadow_trajectory(row)

    assert enriched["created_tasks"] == []
    assert enriched["trajectory"] == {
        "approvals": [],
        "task_outcomes": [],
        "agent_runs": [],
        "tool_calls": [],
        "verifications": [],
    }
