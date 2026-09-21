from assistx import intent_orchestrator as io
from assistx import my_jev_policy
from assistx import neo4j_client


class _Neo:
    def __init__(self):
        self.recorded = []

    def record_intent_policy_shadow(
        self,
        intent_id,
        evidence,
    ):
        self.recorded.append(
            (intent_id, evidence)
        )


class _Queue:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.enqueued = []

    def enqueue(self, function, payload):
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.enqueued.append((function, payload))


def test_orchestrator_records_shadow_evidence(
    monkeypatch,
):
    neo = _Neo()
    evidence = {
        "response": {
            "contract": "assistx-agent-policy-v1"
        }
    }
    monkeypatch.setattr(
        my_jev_policy,
        "request_policy_shadow",
        lambda intent: evidence,
    )

    io._record_my_jev_policy_shadow(
        neo,
        {
            "id": "intent-3",
            "text": "hello",
        },
    )

    assert neo.recorded == [
        ("intent-3", evidence)
    ]


def test_shadow_failure_is_contained_inside_observer_job(
    monkeypatch,
):
    neo = _Neo()

    def fail(intent):
        raise RuntimeError("sidecar unavailable")

    monkeypatch.setattr(
        my_jev_policy,
        "request_policy_shadow",
        fail,
    )

    io._record_my_jev_policy_shadow(
        neo,
        {
            "id": "intent-4",
            "text": "hello",
        },
    )
    assert neo.recorded == []


def test_shadow_disabled_does_not_enqueue(
    monkeypatch,
):
    queue = _Queue()
    monkeypatch.delenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        raising=False,
    )
    monkeypatch.setattr(
        io,
        "get_q",
        lambda: queue,
    )

    io._enqueue_my_jev_policy_shadow(
        {
            "id": "intent-disabled",
            "text": "hello",
        }
    )

    assert queue.enqueued == []


def test_shadow_enqueue_uses_sanitized_snapshot(
    monkeypatch,
):
    queue = _Queue()
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        io,
        "get_q",
        lambda: queue,
    )

    io._enqueue_my_jev_policy_shadow(
        {
            "id": "intent-5",
            "source": "signal",
            "text": "inspect this",
            "classification": "query",
            "policy_action": "legacy_answer",
            "metadata_json": '{"speaker_verified":true}',
            "private_runtime_object": object(),
        }
    )

    assert len(queue.enqueued) == 1
    function, payload = queue.enqueued[0]
    assert (
        function
        is io.record_my_jev_policy_shadow_job
    )
    assert payload == {
        "id": "intent-5",
        "source": "signal",
        "text": "inspect this",
        "classification": "query",
        "policy_action": "legacy_answer",
        "metadata_json": '{"speaker_verified":true}',
    }


def test_shadow_enqueue_failure_never_reopens_live_work(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        io,
        "get_q",
        lambda: _Queue(fail=True),
    )

    io._enqueue_my_jev_policy_shadow(
        {
            "id": "intent-6",
            "text": "hello",
        }
    )


def test_shadow_job_owns_and_closes_its_neo4j_client(
    monkeypatch,
):
    neo = _Neo()
    neo.closed = False

    def close():
        neo.closed = True

    neo.close = close
    observed = []
    monkeypatch.setattr(
        neo4j_client,
        "Neo4jClient",
        lambda: neo,
    )
    monkeypatch.setattr(
        io,
        "_record_my_jev_policy_shadow",
        lambda client, intent: observed.append(
            (client, intent)
        ),
    )

    payload = {
        "id": "intent-7",
        "text": "observe me",
    }
    io.record_my_jev_policy_shadow_job(payload)

    assert observed == [(neo, payload)]
    assert neo.closed is True
