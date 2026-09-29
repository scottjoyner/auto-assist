from types import SimpleNamespace

import pytest

from assistx.headroom_adapter import compress_messages_with_headroom


def fake_compress(messages, *, model):
    assert model == "fixture-model"
    return SimpleNamespace(
        messages=[{"role": "user", "content": "compressed"}],
        tokens_before=100,
        tokens_after=40,
        tokens_saved=60,
        compression_ratio=0.60,
        transforms_applied=["router:smart_crusher:0.60"],
    )


def test_headroom_adapter_normalizes_public_compress_result():
    result = compress_messages_with_headroom(
        [{"role": "user", "content": "large payload"}],
        model="fixture-model",
        compress_fn=fake_compress,
    )
    assert result.tokens_before == 100
    assert result.tokens_after == 40
    assert result.tokens_saved == 60
    assert result.compression_ratio == 0.60
    assert result.transforms_applied == ("router:smart_crusher:0.60",)
    assert result.messages[0]["content"] == "compressed"


def test_headroom_adapter_requires_model():
    with pytest.raises(ValueError, match="model"):
        compress_messages_with_headroom([], model="", compress_fn=fake_compress)


def test_headroom_adapter_rejects_malformed_result():
    def bad(_messages, *, model):
        return SimpleNamespace(messages="not-a-list")

    with pytest.raises(ValueError, match="messages"):
        compress_messages_with_headroom([], model="fixture-model", compress_fn=bad)
