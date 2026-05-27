from assistx.paperclip_poller import _sync_issue


class _Result:
    def single(self):
        return None

    def consume(self):
        return None


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, *args, **kwargs):
        return _Result()


class _FakeNeo:
    def __init__(self):
        self.events = []

    def _session(self):
        return _Session()

    def ingest_paperclip_event(self, **kwargs):
        self.events.append(kwargs)


class _FakePaperclip:
    def __init__(self):
        self.fetched_runs = []
        self.fetched_outputs = []
        self.historical_runs = []

    def get_run(self, run_id):
        self.fetched_runs.append(run_id)
        return {"agentId": "hermes-agent"}

    def get_run_output(self, run_id):
        self.fetched_outputs.append(run_id)
        return ""

    def list_runs(self, issue_id=None, limit=50):
        return self.historical_runs


def test_sync_issue_prefers_live_execution_run_id():
    neo = _FakeNeo()
    pc = _FakePaperclip()
    issue = {
        "id": "issue-1",
        "status": "done",
        "updatedAt": "2026-05-26T20:00:00Z",
        "executionRunId": "execution-run-1",
        "currentRunId": "stale-current-run",
        "checkoutRunId": "checkout-run-1",
    }

    assert _sync_issue(neo, pc, issue, last_poll_ts=None) is True
    assert pc.fetched_runs == ["execution-run-1"]
    assert pc.fetched_outputs == ["execution-run-1"]
    assert neo.events[0]["paperclip_run_id"] == "execution-run-1"
    assert "execution-run-1" in neo.events[0]["event_id"]


def test_sync_issue_uses_checkout_run_when_no_execution_run_exists():
    neo = _FakeNeo()
    pc = _FakePaperclip()
    issue = {"id": "issue-2", "status": "in_progress", "checkoutRunId": "checkout-run-2"}

    assert _sync_issue(neo, pc, issue, last_poll_ts=None) is True
    assert neo.events[0]["paperclip_run_id"] == "checkout-run-2"


def test_sync_issue_retains_successful_run_after_handoff_clears_live_pointer():
    neo = _FakeNeo()
    pc = _FakePaperclip()
    pc.historical_runs = [
        {"runId": "recovery-run", "status": "failed", "agentId": "legacy-process"},
        {"runId": "worker-run", "status": "succeeded", "agentId": "hermes-agent"},
    ]
    issue = {"id": "issue-3", "status": "blocked", "assigneeAgentId": "legacy-process"}

    assert _sync_issue(neo, pc, issue, last_poll_ts=None) is True
    assert pc.fetched_runs == []
    assert pc.fetched_outputs == ["worker-run"]
    assert neo.events[0]["paperclip_run_id"] == "worker-run"
    assert neo.events[0]["paperclip_agent_id"] == "hermes-agent"
    assert neo.events[0]["payload"]["issue"]["status"] == "blocked"


def test_sync_issue_does_not_replace_task_run_with_unscoped_timer_run():
    neo = _FakeNeo()
    pc = _FakePaperclip()
    pc.historical_runs = [
        {
            "runId": "timer-run",
            "status": "succeeded",
            "agentId": "hermes-agent",
            "contextSnapshot": {"wakeReason": "heartbeat_timer"},
        },
        {
            "runId": "task-run",
            "status": "timed_out",
            "agentId": "hermes-agent",
            "contextSnapshot": {"issueId": "issue-4"},
        },
    ]
    pc.get_run = lambda run_id: pc.historical_runs[0]
    issue = {"id": "issue-4", "status": "done", "executionRunId": "timer-run"}

    assert _sync_issue(neo, pc, issue, last_poll_ts=None) is True
    assert neo.events[0]["paperclip_run_id"] == "task-run"
    assert pc.fetched_outputs == ["task-run"]
