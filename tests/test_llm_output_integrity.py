"""Provider output integrity: SSE charset pinning and healthy-success credit.

Two defects are covered here, both reproduced from the live endpoint at
127.0.0.1:1234 (LM Studio, model ``probe2b``):

* The live SSE response carries ``Content-Type: text/event-stream`` with **no**
  ``charset`` parameter.  ``requests`` resolves that to ISO-8859-1, so UTF-8 model
  output decoded as mojibake.  Before the fix the stream helpers never overrode
  the encoding.
* A syntactically valid HTTP 200 carrying empty/null/non-text/mojibake content
  was indistinguishable from a working provider: it cleared the circuit breaker,
  appended ``True`` to the node success window, reset the (model, node) pair, and
  pushed ``quality_score`` toward 1.0.

This file is pure ASCII in source -- every non-ASCII fixture is built from
``\\uXXXX`` escapes or from an explicit ``bytes.decode`` -- so the test module
cannot itself be the thing that gets re-encoded into the mojibake it tests for.
"""

import io
import json

import pytest
import requests
import urllib3
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers

from assistx.llm import client as llm_client
from assistx.llm import output_integrity

# The live prompt that forced multi-byte output on the wire.
CJK_ECHO = "\u3053\u3093\u306b\u3061\u306f \U0001f38c \u3042\u308a\u304c\u3068\u3046 \u2014 \u65e5\u672c\u8a9e"
# What the very same bytes look like after the ISO-8859-1 decode that
# `requests` performs when the SSE Content-Type declares no charset.
CJK_ECHO_MOJIBAKE = CJK_ECHO.encode("utf-8").decode("latin-1")

NODE = "http://fleet-node/v1"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_fleet_state():
    """Snapshot/restore the module-level fleet accounting these tests assert on."""
    live_maps = (
        llm_client._CB_STATE,
        llm_client._pair_failures,
        llm_client._node_results,
        llm_client._node_ema,
        llm_client._node_ema_at,
    )
    saved = [dict(m) for m in live_maps]
    for m in live_maps:
        m.clear()
    yield
    for m, snap in zip(live_maps, saved, strict=True):
        m.clear()
        m.update(snap)


def _sse_response(body: bytes, content_type: str = "text/event-stream") -> requests.Response:
    """A real ``requests.Response`` wired the way ``HTTPAdapter.build_response``
    wires a live one: ``raw`` is a real urllib3 body and ``encoding`` is derived
    from the headers by ``requests`` itself."""
    headers = CaseInsensitiveDict({"Content-Type": content_type})
    resp = requests.Response()
    resp.status_code = 200
    resp.headers = headers
    resp.encoding = get_encoding_from_headers(headers)
    resp.raw = urllib3.HTTPResponse(
        body=io.BytesIO(body),
        status=200,
        headers=dict(headers),
        preload_content=False,
    )
    return resp


def _sse_body(content: str) -> bytes:
    chunk = {"choices": [{"delta": {"content": content}}]}
    finish = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    return (
        "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        "data: " + json.dumps(finish, ensure_ascii=False) + "\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")


class _Resp:
    """Minimal non-streaming response stand-in for ``_chat_openai``."""

    def __init__(self, content, status_code=200):
        self.status_code = status_code
        self._payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _route_one_candidate(monkeypatch, content):
    """Point chat() at a single hot candidate whose answer is *content*.

    Returns ``(posts, perf)``: the URLs posted to, and the ``ok`` flag of every
    ``_record_perf`` sample (``None`` when it was never called).
    """
    monkeypatch.setattr(llm_client, "FALLBACK_MODELS", [])
    monkeypatch.setattr(llm_client, "fleet_base_urls_for", lambda _m: [NODE])
    monkeypatch.setattr(llm_client, "_candidate_models", lambda _m=None: ["m1"])
    posts = []

    def fake_post(url, **kwargs):
        posts.append(url)
        return _Resp(content)

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    perf = []
    monkeypatch.setattr(
        llm_client,
        "_record_perf",
        lambda *a, **k: perf.append(k["ok"] if "ok" in k else a[4]),
    )
    return posts, perf


# --------------------------------------------------------------------------- #
# 1. SSE streams are pinned to UTF-8 when Content-Type declares no charset
# --------------------------------------------------------------------------- #
def test_charset_less_sse_resolves_to_iso_8859_1_in_requests():
    """Pins the premise of the defect: this is genuinely what requests does.

    The non-streaming JSON path is unaffected, which is why only the streaming
    helpers need the override.
    """
    assert get_encoding_from_headers(CaseInsensitiveDict({"Content-Type": "text/event-stream"})) == "ISO-8859-1"
    assert get_encoding_from_headers(CaseInsensitiveDict({"Content-Type": "application/json"})) == "utf-8"


def test_stream_openai_fleet_decodes_utf8_when_content_type_has_no_charset(monkeypatch):
    """End-to-end through the real streaming helper with real bytes on the wire."""
    monkeypatch.setattr(
        llm_client.requests,
        "post",
        lambda *a, **k: _sse_response(_sse_body(CJK_ECHO)),
    )
    events = list(llm_client._stream_openai_fleet([{"role": "user", "content": "hi"}], "m1", NODE))
    assert "".join(e["data"] for e in events if e["event"] == "delta") == CJK_ECHO


def test_stream_openai_decodes_utf8_when_content_type_has_no_charset(monkeypatch):
    monkeypatch.setattr(
        llm_client.requests,
        "post",
        lambda *a, **k: _sse_response(_sse_body(CJK_ECHO)),
    )
    events = list(llm_client._stream_openai([{"role": "user", "content": "hi"}], "m1"))
    assert "".join(e["data"] for e in events if e["event"] == "delta") == CJK_ECHO


def test_stream_ollama_ndjson_is_unaffected_by_the_charset_default(monkeypatch):
    """Guard against over-applying the fix: Ollama's ``application/x-ndjson``
    stream is not a ``text/*`` type, so ``requests`` leaves ``encoding`` unset and
    the UTF-8 bytes reach ``json.loads`` intact.  No pin is applied there."""
    monkeypatch.setattr(llm_client, "OLLAMA_HOST", "http://ollama")
    body = (
        json.dumps({"message": {"content": CJK_ECHO}}, ensure_ascii=False) + "\n"
        + json.dumps({"done": True}, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        llm_client.requests,
        "post",
        lambda *a, **k: _sse_response(body, content_type="application/x-ndjson"),
    )
    events = list(llm_client._stream_ollama([{"role": "user", "content": "hi"}], "m1"))
    assert "".join(e["data"] for e in events if e["event"] == "delta") == CJK_ECHO


def test_unpinned_sse_yields_the_observed_mojibake():
    """Control: without the pin the identical bytes are mojibake.

    This is exactly what the live probe recorded: the same response decoded as
    UTF-8 and as ISO-8859-1 (ev2_content_utf8.txt / ev2_content_latin1.txt).
    """
    resp = _sse_response(_sse_body(CJK_ECHO))
    first = next(iter(resp.iter_lines(decode_unicode=True)))
    assert json.loads(first[6:])["choices"][0]["delta"]["content"] == CJK_ECHO_MOJIBAKE
    assert CJK_ECHO_MOJIBAKE != CJK_ECHO
    assert output_integrity.classify_text(CJK_ECHO_MOJIBAKE) == output_integrity.MOJIBAKE_TEXT


def test_declared_charset_is_authoritative_and_not_overridden():
    declared_utf8 = _sse_response(_sse_body(CJK_ECHO), content_type="text/event-stream; charset=utf-8")
    assert output_integrity.pin_stream_encoding(declared_utf8) is False
    assert declared_utf8.encoding == "utf-8"

    declared_latin1 = _sse_response(_sse_body(CJK_ECHO), content_type="text/event-stream; charset=iso-8859-1")
    assert output_integrity.pin_stream_encoding(declared_latin1) is False
    assert declared_latin1.encoding.lower() == "iso-8859-1"


@pytest.mark.parametrize(
    "content_type",
    ["text/event-stream", "text/event-stream; charset=", "text/plain", ""],
)
def test_pin_applies_whenever_no_charset_is_declared(content_type):
    resp = _sse_response(b"", content_type=content_type)
    assert output_integrity.pin_stream_encoding(resp) is True
    assert resp.encoding == "utf-8"


def test_declared_charset_parsing():
    assert output_integrity.declared_charset("text/event-stream; charset=UTF-8") == "UTF-8"
    assert output_integrity.declared_charset('text/event-stream; boundary="x"; charset="utf-8"') == "utf-8"
    assert output_integrity.declared_charset("text/event-stream") is None
    assert output_integrity.declared_charset(None) is None


# --------------------------------------------------------------------------- #
# 2. classify_text: which provider outputs earn healthy-success credit
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("a real answer", output_integrity.OK),
        ("what is 2+2? It is 4.", output_integrity.OK),
        ("caf\u00e9 \u2014 ok", output_integrity.OK),
        ("\u00bfQu\u00e9? S\u00ed", output_integrity.OK),
        (CJK_ECHO, output_integrity.OK),
        (CJK_ECHO_MOJIBAKE, output_integrity.MOJIBAKE_TEXT),
        ("", output_integrity.EMPTY_CONTENT),
        ("   \n\t ", output_integrity.WHITESPACE_ONLY),
        (None, output_integrity.NON_TEXT_CONTENT),
        ({"text": "hi"}, output_integrity.NON_TEXT_CONTENT),
        (["hi"], output_integrity.NON_TEXT_CONTENT),
        (123, output_integrity.NON_TEXT_CONTENT),
        ("??", output_integrity.OK),
        ("??????", output_integrity.PLACEHOLDER_RUN),
        ("? \ufffd ? \ufffd", output_integrity.UNDECODABLE_TEXT),
        ("answer \ufffd replacement", output_integrity.UNDECODABLE_TEXT),
    ],
)
def test_classify_text(value, reason):
    assert output_integrity.classify_text(value) == reason
    assert output_integrity.is_usable(reason) is (reason == output_integrity.OK)


def test_output_integrity_module_source_is_pure_ascii():
    """The detector must not be able to become a source of mojibake."""
    with open(output_integrity.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert max((ord(c) for c in source), default=0) < 128


# --------------------------------------------------------------------------- #
# 3. chat(): unusable output gets no healthy-success credit, and nothing else
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("empty string", ""),
        ("whitespace", "  \n "),
        ("null", None),
        ("content-block list", [{"type": "text", "text": "hi"}]),
        ("content dict", {"text": "hi"}),
        ("mojibake", CJK_ECHO_MOJIBAKE),
        ("placeholder run", "?????"),
    ],
)
def test_unusable_output_earns_no_healthy_success_credit(monkeypatch, label, content):
    llm_client._CB_STATE["m1"] = {"failures": 2.0, "open_until": 0.0}
    llm_client._pair_failures["m1|" + NODE] = 2
    posts, perf = _route_one_candidate(monkeypatch, content)

    out = llm_client.chat([{"role": "user", "content": "hi"}], model="m1")

    # caller-visible return behaviour is untouched
    assert out == content, label
    # ... and no success was recorded anywhere
    assert llm_client._CB_STATE["m1"] == {"failures": 2.0, "open_until": 0.0}, label
    assert llm_client._pair_failure_count("m1", NODE) == 2, label
    assert True not in llm_client._node_results.get(NODE, []), label
    assert perf == [], label


def test_unusable_output_does_not_advance_routing_or_incur_failure_penalty(monkeypatch):
    """Provider ordering preserved: one POST, no penalty, no fallthrough."""
    posts, perf = _route_one_candidate(monkeypatch, "")

    llm_client.chat([{"role": "user", "content": "hi"}], model="m1")

    assert len(posts) == 1
    assert llm_client._pair_failure_count("m1", NODE) == 0
    assert perf == []


def test_healthy_output_still_earns_full_healthy_success_credit(monkeypatch):
    llm_client._CB_STATE["m1"] = {"failures": 2.0, "open_until": 0.0}
    llm_client._pair_failures["m1|" + NODE] = 3
    posts, perf = _route_one_candidate(monkeypatch, "a real answer")

    out = llm_client.chat([{"role": "user", "content": "hi"}], model="m1")

    assert out == "a real answer"
    assert llm_client._CB_STATE["m1"] == {"failures": 0.0, "open_until": 0.0}
    assert llm_client._pair_failure_count("m1", NODE) == 0
    assert llm_client._node_results[NODE] == [True]
    assert perf == [True]
    assert len(posts) == 1


def test_hard_failure_penalties_are_unchanged(monkeypatch):
    """A real provider error must still penalise, fail over, and raise."""
    llm_client._CB_STATE["m1"] = {"failures": 0.0, "open_until": 0.0}
    monkeypatch.setattr(llm_client, "FALLBACK_MODELS", [])
    monkeypatch.setattr(llm_client, "fleet_base_urls_for", lambda _m: [NODE, "http://node2/v1"])
    monkeypatch.setattr(llm_client, "_candidate_models", lambda _m=None: ["m1", "m2"])
    perf = []
    monkeypatch.setattr(
        llm_client,
        "_record_perf",
        lambda *a, **k: perf.append(k["ok"] if "ok" in k else a[4]),
    )

    def boom(url, **kwargs):
        raise RuntimeError("upstream 500")

    monkeypatch.setattr(llm_client.requests, "post", boom)

    with pytest.raises(RuntimeError):
        llm_client.chat([{"role": "user", "content": "hi"}], model="m1")

    # every candidate x node pair attempted -> routing order intact
    for model in ("m1", "m2"):
        for node in (NODE, "http://node2/v1"):
            assert llm_client._pair_failure_count(model, node) == 1
    assert perf == [False, False, False, False]
    # two attempts per candidate (one per node) counted against each breaker
    assert llm_client._CB_STATE["m1"]["failures"] == 2.0
    assert llm_client._CB_STATE["m2"]["failures"] == 2.0
