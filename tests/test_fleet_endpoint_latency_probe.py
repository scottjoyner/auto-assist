import json

from assistx.fleet_endpoint_latency_probe import probe_openai_stream


class _FakeResponse:
    def __init__(self, lines):
        self._lines = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def readline(self):
        return next(self._lines, b"")


def _clock(values):
    it = iter(values)
    return lambda: next(it)


def test_reasoning_token_counts_as_ttft_before_visible_content():
    lines = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
        b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"OK"}}]}\n',
        b'data: {"usage":{"prompt_tokens":7,"completion_tokens":2},"choices":[]}\n',
        b"data: [DONE]\n",
    ]

    result = probe_openai_stream(
        base_url="http://node:1234/v1",
        model_id="reasoner",
        opener=lambda *args, **kwargs: _FakeResponse(lines),
        clock=_clock([0.0, 0.010, 0.020, 0.030, 0.080, 0.090, 0.100]),
    )

    assert result["first_sse_event_ms"] == 20.0
    assert result["ttft_ms"] == 30.0
    assert result["first_reasoning_ms"] == 30.0
    assert result["first_content_ms"] == 80.0
    assert result["wall_ms"] == 100.0
    assert result["ttft_basis"] == "reasoning_or_content"
    assert set(result["authority"].values()) == {False}


def test_content_only_model_uses_first_content_as_ttft():
    lines = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"OK"}}]}\n',
        b"data: [DONE]\n",
    ]

    result = probe_openai_stream(
        base_url="http://node:1234/v1",
        model_id="plain",
        opener=lambda *args, **kwargs: _FakeResponse(lines),
        clock=_clock([0.0, 0.010, 0.020, 0.040, 0.050]),
    )

    assert result["ttft_ms"] == 40.0
    assert result["first_reasoning_ms"] is None
    assert result["first_content_ms"] == 40.0


def test_probe_rejects_metadata_only_stream():
    lines = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
        b"data: [DONE]\n",
    ]

    try:
        probe_openai_stream(
            base_url="http://node:1234/v1",
            model_id="empty",
            opener=lambda *args, **kwargs: _FakeResponse(lines),
            clock=_clock([0.0, 0.010, 0.020, 0.030]),
        )
    except ValueError as exc:
        assert "no reasoning or content token" in str(exc)
    else:
        raise AssertionError("expected metadata-only stream to be rejected")


def test_probe_request_is_bounded_and_has_no_tools():
    captured = {}

    def opener(request, **kwargs):
        captured["body"] = json.loads(request.data)
        return _FakeResponse(
            [
                b'data: {"choices":[{"delta":{"content":"OK"}}]}\n',
                b"data: [DONE]\n",
            ]
        )

    result = probe_openai_stream(
        base_url="http://node:1234/v1",
        model_id="plain",
        max_tokens=4,
        opener=opener,
        clock=_clock([0.0, 0.010, 0.020, 0.030]),
    )

    assert captured["body"]["max_tokens"] == 4
    assert "tools" not in captured["body"]
    assert result["evidence_only"] is True
