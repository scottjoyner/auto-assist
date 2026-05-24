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
