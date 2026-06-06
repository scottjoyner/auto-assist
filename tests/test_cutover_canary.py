from assistx.canary import CutoverCanaryTarget, SignedIngestSample, run_cutover_canary


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.content = b"1"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.dispatch_polls = 0
        self.calls = []

    def post(self, url, data=None, headers=None, auth=None, timeout=None):
        self.calls.append(("post", url, auth, headers))
        if url.endswith("/api/voice/events"):
            return _Response({"signal_event_id": "sig-1", "intent_id": "intent-1", "task_id": "task-1"})
        if url.endswith("/api/dispatch"):
            return _Response({"dispatch_id": "dispatch-1", "paperclip_issue_id": "issue-1"})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, params=None, auth=None, timeout=None):
        self.calls.append(("get", url, auth, params))
        assert url.endswith("/api/dispatches")
        self.dispatch_polls += 1
        status = "OPEN" if self.dispatch_polls == 1 else "COMPLETED"
        return _Response({"items": [{"paperclip_issue_id": "issue-1", "status": status}]})


def test_cutover_canary_posts_signed_ingest_creates_dispatch_and_waits_for_terminal_disposition(monkeypatch):
    session = _Session()
    sample = SignedIngestSample(
        endpoint="/api/voice/events",
        payload={"event_id": "evt-1", "event_type": "voice_enrolled", "text": "enroll me"},
        signature_header="X-Voice-Signature",
        signature="sha256=signed",
        auth_user="neo4j",
        auth_pass="pass",
    )

    result = run_cutover_canary(
        base_url="http://localhost:8000",
        target=CutoverCanaryTarget(worker_target="Hermes Agent", expected_disposition="COMPLETED"),
        signed_enrollment_sample=sample,
        timeout_s=1.0,
        poll_interval_s=0.0,
        session=session,
    )

    assert result.ingest_response["task_id"] == "task-1"
    assert result.dispatch_response["paperclip_issue_id"] == "issue-1"
    assert result.terminal_dispatch["status"] == "COMPLETED"
    assert any(call[0] == "post" and call[1].endswith("/api/voice/events") for call in session.calls)
    assert any(call[0] == "post" and call[1].endswith("/api/dispatch") for call in session.calls)
    assert any(call[0] == "get" and call[1].endswith("/api/dispatches") for call in session.calls)
