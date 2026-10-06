import pytest

from assistx.harnessrouter_adapter import (
    HarnessRouterAdapter,
    HarnessRouterConfig,
)


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/v1/harnesses":
            return {"data": [{"id": "codex"}]}
        if path == "/v1/models":
            return {"data": [{"id": "fixture-model"}]}
        if path == "/v1/responses":
            return {
                "id": "resp_1", "status": "in_progress", "model": body["model"],
                "metadata": {"session_id": "sess_1"},
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            }
        if path == "/v1/responses/resp_1":
            return {
                "id": "resp_1", "status": "completed", "model": "fixture-model",
                "metadata": {"session_id": "sess_1"},
                "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
            }
        if path == "/v1/responses/resp_1/cancel":
            return {"id": "resp_1", "status": "cancelled", "metadata": {"session_id": "sess_1"}}
        raise AssertionError(path)


def test_discovery_is_read_only_and_reports_capabilities():
    transport = FakeTransport()
    adapter = HarnessRouterAdapter(
        HarnessRouterConfig(base_url="http://hr.local"), request_fn=transport,
    )
    projection = adapter.discover()
    assert projection["authority"] == "discovery-only"
    assert projection["canary_enabled"] is False
    assert transport.calls == [
        ("GET", "/v1/harnesses", None),
        ("GET", "/v1/models", None),
    ]


def test_canary_requires_config_and_per_call_opt_in():
    transport = FakeTransport()
    adapter = HarnessRouterAdapter(
        HarnessRouterConfig(base_url="http://hr.local"), request_fn=transport,
    )
    with pytest.raises(PermissionError, match="disabled"):
        adapter.submit_canary(
            "hello", harness_id="codex", model="fixture-model", explicit_opt_in=True,
        )
    assert transport.calls == []

    adapter = HarnessRouterAdapter(
        HarnessRouterConfig(base_url="http://hr.local", canary_enabled=True,
                            allowed_harness_ids=("codex",)),
        request_fn=transport,
    )
    with pytest.raises(PermissionError, match="explicit"):
        adapter.submit_canary(
            "hello", harness_id="codex", model="fixture-model", explicit_opt_in=False,
        )


def test_canary_submission_poll_and_cancel_normalize_receipts():
    transport = FakeTransport()
    adapter = HarnessRouterAdapter(
        HarnessRouterConfig(
            base_url="http://hr.local", canary_enabled=True,
            allowed_harness_ids=("codex",),
        ),
        request_fn=transport,
    )
    submitted = adapter.submit_canary(
        "reply exactly ok", harness_id="codex",
        model="fixture-model", explicit_opt_in=True,
    )
    assert submitted.response_id == "resp_1"
    assert submitted.status == "in_progress"
    assert submitted.session_id == "sess_1"
    assert submitted.total_tokens == 12
    assert transport.calls[-1][2]["background"] is True
    assert transport.calls[-1][2]["stream"] is False

    completed = adapter.get_response("resp_1")
    assert completed.status == "completed"
    assert completed.total_tokens == 13

    cancelled = adapter.cancel_canary(
        "resp_1", explicit_opt_in=True, harness_id="codex",
    )
    assert cancelled.status == "cancelled"


def test_non_allowlisted_harness_is_refused_before_network_call():
    transport = FakeTransport()
    adapter = HarnessRouterAdapter(
        HarnessRouterConfig(
            base_url="http://hr.local", canary_enabled=True,
            allowed_harness_ids=("codex",),
        ),
        request_fn=transport,
    )
    with pytest.raises(PermissionError, match="allowlisted"):
        adapter.submit_canary(
            "hello", harness_id="other", model="fixture-model", explicit_opt_in=True,
        )
    assert transport.calls == []
