from assistx import intent_orchestrator as io
from assistx import my_jev_policy


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


def test_shadow_failure_never_blocks_orchestration(
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
