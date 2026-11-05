"""Bounded, synthetic fragment-level validation of scoped Hermes SSE handling."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from assistx import hermes_router_relay as relay

_FIRST = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
_DONE = b"data: [DONE]\n\n"


class _Fragments(httpx.AsyncByteStream):
    def __init__(self, parts: list[bytes]):
        self.parts = parts
        self.closed = False

    async def __aiter__(self):
        for fragment in self.parts:
            yield fragment

    async def aclose(self):
        self.closed = True


def _collect(parts: list[bytes]) -> tuple[list[bytes], bool]:
    source = _Fragments(parts)
    response = httpx.Response(
        200, headers={"Content-Type": "text/event-stream"},
        stream=source, request=httpx.Request(
            "POST", "http://llm-router:8088/v1/chat/completions",
        ),
    )

    async def run():
        frames = []
        try:
            async for frame in relay._validated_sse(response):
                frames.append(frame)
            return frames
        finally:
            await response.aclose()

    return asyncio.run(run()), source.closed


def test_fragmented_realistic_stream_has_clean_terminal_marker():
    frames, closed = _collect([
        _FIRST[:8], _FIRST[8:-1], _FIRST[-1:] + _DONE[:7],
        _DONE[7:],
    ])
    assert frames == [_FIRST, _DONE]
    assert closed


def test_crlf_delimiters_preserved_with_fragmented_transport():
    frames, closed = _collect([
        _FIRST.replace(b"\n", b"\r\n")[:17],
        _FIRST.replace(b"\n", b"\r\n")[17:],
        _DONE.replace(b"\n", b"\r\n"),
    ])
    assert frames == [
        _FIRST.replace(b"\n", b"\r\n"),
        _DONE.replace(b"\n", b"\r\n"),
    ]
    assert closed


@pytest.mark.parametrize("bad", [
    b'data: {"choices":[]}\n\n',  # missing [DONE]
    b'data: {"choices":[]}\n\ndata: [DONE]\n\ndata: {"choices":[]}\n\n',
    b'data: {"choices":[]}\n\ndata: [DONE]\n\nx',
    b'data: {"choices":[]}\n\ndata: [DONE]',
    b'data: {"choices":[]}\n\ndata: [DONE]\n\n\n\n',
    b"data: {" + b"x" * (relay._MAX_SSE_FRAME + 1) + b"}\n\n",
    b'data: {"choices":[],"choices":[]}\n\n',
    b'data: {"choices":{}}\n\n',
    b'data: {"choices":[],"value":NaN}\n\n',
    b'event: error\ndata: {"choices":[]}\n\n',
    b'data: [DONE]\n\ndata: [DONE]\n\n',
])
def test_invalid_or_incomplete_sse_never_yields_terminal_done(bad):
    source = _Fragments([bad])
    response = httpx.Response(
        200, headers={"Content-Type": "text/event-stream"},
        stream=source, request=httpx.Request(
            "POST", "http://llm-router:8088/v1/chat/completions",
        ),
    )

    async def run():
        frames = []
        try:
            async for frame in relay._validated_sse(response):
                frames.append(frame)
        finally:
            await response.aclose()
        return frames

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert source.closed


def test_genuine_stream_with_done_is_not_treated_as_failed():
    frames, closed = _collect([_FIRST + _DONE])
    assert frames[-1] == _DONE
    assert len(frames) == 2
    assert closed
