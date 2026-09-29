import json
from types import SimpleNamespace

from assistx.context_compression_policy import compress_messages_hybrid


def fake_headroom(messages, *, model):
    return SimpleNamespace(
        messages=[{"role": "tool", "content": "compressed"}],
        tokens_before=100,
        tokens_after=40,
        tokens_saved=60,
        compression_ratio=0.60,
        transforms_applied=["fixture"],
    )


def test_escape_sensitive_tool_json_uses_lossless_fallback():
    marker = 'literal\\nnot-newline:"quoted"'
    messages = [
        {"role": "user", "content": "Return target exactly."},
        {
            "role": "tool",
            "content": '{\n  "target": "literal\\\\nnot-newline:\\"quoted\\"",\n  "n": 1\n}',
        },
    ]
    result = compress_messages_hybrid(
        messages,
        model="fixture-model",
        compress_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Headroom must not be called")
        ),
    )
    assert result.bypassed_headroom is True
    assert result.strategy == "lossless_json_fallback"
    assert result.bypass_reason == "escape_sensitive_tool_json"
    decoded = json.loads(result.messages[-1]["content"])
    assert decoded["target"] == marker
    assert "\n  " not in result.messages[-1]["content"]


def test_normal_structured_tool_output_can_use_headroom():
    messages = [
        {"role": "user", "content": "Find warnings."},
        {"role": "tool", "content": '[{"status":"ok"},{"status":"warning"}]'},
    ]
    result = compress_messages_hybrid(
        messages,
        model="fixture-model",
        compress_fn=fake_headroom,
    )
    assert result.bypassed_headroom is False
    assert result.strategy == "headroom"
    assert result.headroom is not None
    assert result.headroom.tokens_saved == 60
