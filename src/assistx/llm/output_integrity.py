"""Provider output integrity: is a provider's HTTP 200 answer usable at all?

Two distinct defects produced indistinguishable "healthy" completions:

1. **Declared charset absent.**  ``requests`` resolves any ``text/*`` Content-Type
   with no ``charset`` parameter to ISO-8859-1.  LM Studio (and bare
   llama-server) answer SSE with ``Content-Type: text/event-stream`` and no
   charset, so UTF-8 model output was decoded byte-per-character and reached
   callers as the familiar ``a-tilde`` / U+0081 / U+0093 triple-per-character
   garbage.  :func:`pin_stream_encoding` closes this at the transport boundary,
   before a single delta is parsed.

2. **Syntactically valid, semantically empty.**  ``content: null`` (tool-call-only
   turn or a gateway that dropped it), ``content: ""``, whitespace, or a
   non-string content-block list all used to be indistinguishable from a real
   answer: ``chat()`` returned the value and the (model, node) pair was marked
   healthy, the breaker was cleared, and ``quality_score`` trended to 1.0.
   :func:`classify_text` is the single dependency-free answer to "can a caller
   consume this, and if not, why".

This module performs no I/O, holds no state, and makes no routing decision.
Callers use :func:`classify_text` to decide whether a response earned
*healthy-success credit*; they keep returning the provider's value unchanged, so
caller-visible behaviour and provider ordering are unaffected.

Written with ``chr()`` rather than literals for the non-ASCII markers so this
module stays pure ASCII in source and cannot itself be re-encoded into mojibake
by an editor or a patch tool -- precisely the failure it exists to catch.
"""

from __future__ import annotations

from typing import Any

# --- reason codes ------------------------------------------------------------
# A closed set: they are returned to callers and used as stable operator-facing
# strings, so they are append-only.
OK = "ok"
EMPTY_CONTENT = "empty_content"
WHITESPACE_ONLY = "whitespace_only"
NON_TEXT_CONTENT = "non_text_content"
UNDECODABLE_TEXT = "undecodable_text"
MOJIBAKE_TEXT = "mojibake_text"
PLACEHOLDER_RUN = "placeholder_run"

DEFECT_REASONS = frozenset({
    EMPTY_CONTENT,
    WHITESPACE_ONLY,
    NON_TEXT_CONTENT,
    UNDECODABLE_TEXT,
    MOJIBAKE_TEXT,
    PLACEHOLDER_RUN,
})

# --- non-ASCII markers (chr() so this source stays pure ASCII) ---------------
_REPLACEMENT = chr(0xFFFD)   # lossy decoder's "cannot represent this"
_QUESTION = chr(0x003F)      # lossy decoder's other fallback

# U+0080..U+00BF: the codepoints a UTF-8 continuation byte maps to when a
# single-byte codec (latin-1 / cp1252) reinterprets a UTF-8 stream.
_CONT_MIN = chr(0x80)
_CONT_MAX = chr(0xBF)

# Leading codepoints of that signature: U+00C3, U+00C2, U+00E2.  The U+00E2 lead
# is what turns the E2 80 xx punctuation family (em dash, curly quotes) into the
# familiar three-character mojibake runs.
_MOJIBAKE_LEADS = (chr(0xC3), chr(0xC2), chr(0xE2))


def _is_double_encoded(text: str) -> bool:
    """True when *text* carries the UTF-8-read-as-latin-1 signature.

    A mojibake lead character immediately followed by a codepoint in
    U+0080..U+00BF is the tell: those are continuation bytes of a UTF-8 sequence
    that a single-byte codec reinterpreted.  Natural text never does this,
    including genuinely multilingual text, so real Japanese, Chinese, Cyrillic,
    or emoji output is never flagged.
    """
    for i in range(len(text) - 1):
        if text[i] in _MOJIBAKE_LEADS and _CONT_MIN <= text[i + 1] <= _CONT_MAX:
            return True
    return False


def _is_placeholder_run(stripped: str) -> bool:
    """True when the whole answer collapsed to lossy placeholder characters.

    Conservative by design: only an answer that is *entirely* a question mark or
    U+FFFD (after whitespace removal, at least four characters long) is flagged,
    so ordinary prose containing question marks or punctuation is untouched.
    """
    return len(stripped) >= 4 and all(ch in (_QUESTION, _REPLACEMENT) for ch in stripped)


def classify_text(value: Any) -> str:
    """Classify a provider's content field.  Returns one of the reason codes.

    ``value`` is whatever the adapter pulled out of the envelope, so it may be
    ``None``, a dict, a list, or a str.  A non-str value is a defect here rather
    than a caller error: it means the provider returned a shape this adapter
    cannot read as text.
    """
    if not isinstance(value, str):
        return NON_TEXT_CONTENT
    if value == "":
        return EMPTY_CONTENT
    stripped = value.strip()
    if not stripped:
        return WHITESPACE_ONLY
    if _REPLACEMENT in value:
        return UNDECODABLE_TEXT
    if _is_double_encoded(value):
        return MOJIBAKE_TEXT
    if _is_placeholder_run(stripped):
        return PLACEHOLDER_RUN
    return OK


def is_usable(reason: str) -> bool:
    """True when *reason* describes text a caller can consume."""
    return reason == OK


def declared_charset(content_type: str | None) -> str | None:
    """Return the charset parameter of a Content-Type header, or None."""
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip("\"'") or None
    return None


def pin_stream_encoding(response: Any) -> bool:
    """Pin a streaming response to UTF-8 when it declares no charset.

    Must be called after ``raise_for_status()`` and before the first
    ``iter_lines``/``iter_content``: ``requests`` applies its encoding lazily, per
    chunk, so pinning afterwards would leave earlier chunks decoded as latin-1.

    A charset the provider *did* declare is authoritative and left alone.  Returns
    True when the override was applied, so callers can log it once per endpoint
    without a metrics subsystem.
    """
    headers = getattr(response, "headers", None) or {}
    try:
        content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    except Exception:
        return False
    if declared_charset(content_type) is not None:
        return False
    try:
        response.encoding = "utf-8"
    except Exception:
        return False
    return True
