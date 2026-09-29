from assistx import intent_orchestrator as io


class _FakeNeo:
    def __init__(self):
        self.marked = []

    def mark_intent_orchestrated(self, intent_id):
        self.marked.append(intent_id)


def test_intent_policy_action_from_metadata_json():
    intent = {"metadata_json": '{"policy_action":"review_dispatch"}'}
    assert io._intent_policy_action(intent) == "review_dispatch"


def test_task_intent_review_path(monkeypatch):
    neo = _FakeNeo()
    calls = {"review": 0, "task": 0}

    monkeypatch.setattr(io, "_queue_intent_review", lambda *args, **kwargs: calls.__setitem__("review", calls["review"] + 1))
    monkeypatch.setattr(io, "_handle_task", lambda *args, **kwargs: calls.__setitem__("task", calls["task"] + 1))

    io._process_intent(
        neo,
        {
            "id": "intent-review-1",
            "text": "do the thing",
            "classification": "task",
            "policy_action": "review_dispatch",
        },
    )

    assert calls["review"] == 1
    assert calls["task"] == 0
    assert neo.marked == ["intent-review-1"]


def test_task_intent_auto_dispatch_path(monkeypatch):
    neo = _FakeNeo()
    calls = {"review": 0, "task": 0}

    monkeypatch.setattr(io, "_queue_intent_review", lambda *args, **kwargs: calls.__setitem__("review", calls["review"] + 1))
    monkeypatch.setattr(io, "_handle_task", lambda *args, **kwargs: calls.__setitem__("task", calls["task"] + 1))

    io._process_intent(
        neo,
        {
            "id": "intent-auto-1",
            "text": "please build this",
            "classification": "task",
            "policy_action": "auto_dispatch_eligible",
        },
    )

    assert calls["review"] == 0
    assert calls["task"] == 1
    assert neo.marked == ["intent-auto-1"]


def test_shadow_enqueue_happens_after_live_handler_and_commit(
    monkeypatch,
):
    events = []

    class _OrderedNeo:
        def mark_intent_orchestrated(self, intent_id):
            events.append(("mark", intent_id))

    monkeypatch.setattr(
        io,
        "_handle_task",
        lambda neo, intent: events.append(
            ("task", intent["id"])
        ),
    )
    monkeypatch.setattr(
        io,
        "_enqueue_my_jev_policy_shadow",
        lambda intent: events.append(
            ("shadow", intent["id"])
        ),
    )

    io._process_intent(
        _OrderedNeo(),
        {
            "id": "intent-order-1",
            "text": "please build this",
            "classification": "task",
            "policy_action": "auto_dispatch_eligible",
        },
    )

    assert events == [
        ("task", "intent-order-1"),
        ("mark", "intent-order-1"),
        ("shadow", "intent-order-1"),
    ]


def test_enabled_shadow_never_calls_sidecar_in_live_intent_path(
    monkeypatch,
):
    from assistx import my_jev_policy

    queue_calls = []
    task_calls = []
    neo = _FakeNeo()

    class _Queue:
        def enqueue(self, function, payload):
            queue_calls.append((function, payload))

    def fail_if_called(intent):
        raise AssertionError(
            "sidecar inference must not run in live intent path"
        )

    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        io,
        "get_q",
        lambda: _Queue(),
    )
    monkeypatch.setattr(
        io,
        "_handle_task",
        lambda *args, **kwargs: task_calls.append("task"),
    )
    monkeypatch.setattr(
        my_jev_policy,
        "request_policy_shadow",
        fail_if_called,
    )

    io._process_intent(
        neo,
        {
            "id": "intent-shadow-enabled",
            "text": "perform the legacy task",
            "classification": "task",
            "policy_action": "auto_dispatch_eligible",
        },
    )

    assert task_calls == ["task"]
    assert neo.marked == ["intent-shadow-enabled"]
    assert len(queue_calls) == 1
    function, payload = queue_calls[0]
    assert function is io.record_my_jev_policy_shadow_job
    assert payload["id"] == "intent-shadow-enabled"
