from assistx import my_jev_policy


def test_build_policy_request_separates_semantic_state_from_authority(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_SHADOW_ACTIONS_ALLOWED",
        "false",
    )
    monkeypatch.setenv(
        "MY_JEV_SHADOW_EXTERNAL_ACTIONS_ALLOWED",
        "false",
    )
    request = my_jev_policy.build_policy_request(
        {
            "id": "intent-1",
            "source": "signal",
            "text": "Send the prepared update to the team.",
            "classification": "task",
            "metadata_json": (
                '{"speaker_id":"scott",'
                '"speaker_verified":true,'
                '"available_tools":["send_message"],'
                '"policy_action":"auto_dispatch_eligible"}'
            ),
        }
    )

    assert request["state"]["utterance"].startswith("Send")
    assert request["state"]["speaker_verified"] is True
    assert request["state"]["actions_allowed"] is False
    assert request["constraints"]["actions_allowed"] is False
    assert (
        request["state"]["metadata"]["legacy_policy_action"]
        == "auto_dispatch_eligible"
    )


def test_shadow_disabled_does_not_call_network(
    monkeypatch,
):
    monkeypatch.delenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        raising=False,
    )

    def fail(*args, **kwargs):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        fail,
    )
    assert (
        my_jev_policy.request_policy_shadow(
            {"id": "i", "text": "hello"}
        )
        is None
    )


def test_shadow_request_records_legacy_comparison(
    monkeypatch,
):
    monkeypatch.setenv(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        "true",
    )

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "contract": "assistx-agent-policy-v1",
                "resolved": {
                    "model_route": "chat",
                    "disposition": "chat",
                },
                "assistx": {
                    "policy_action": "answer_inline",
                },
            }

    captured = {}

    def post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(
        my_jev_policy.requests,
        "post",
        post,
    )

    evidence = my_jev_policy.request_policy_shadow(
        {
            "id": "intent-2",
            "source": "web",
            "text": "Explain this error.",
            "classification": "query",
            "policy_action": "legacy_answer",
        }
    )

    assert evidence["shadow"] is True
    assert (
        evidence["legacy"]["classification"]
        == "query"
    )
    assert (
        evidence["response"]["resolved"]["model_route"]
        == "chat"
    )
    assert captured["json"]["state"]["utterance"] == "Explain this error."
