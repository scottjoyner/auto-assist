"""Hermetic regression tests for non-ASCII fleet-node token verification.

``verify_node_token`` guards a fleet-node identity gate. It must always return
a reason string; it must never raise on attacker-controlled header material.
Python's ``hmac.compare_digest`` rejects non-ASCII ``str`` operands, so on the
base revision a token containing any non-ASCII code point escaped the deny path
as an unhandled ``TypeError`` (HTTP 500 instead of the intended 403).

These tests use only synthetic in-process dicts: no Redis/Neo4j/HTTP/service,
no filesystem fixture, no credential read, no live fleet claim.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from assistx.node_identity import verify_node_token

_NON_ASCII_TOKENS = [
    "s\u00e9cret-a",  # Latin-1 supplement (é)
    "\uff21",  # fullwidth Latin capital A
    "secret\u200b-a",  # zero-width space
    "\u65e5\u672c\u8a9e",  # CJK
]


@pytest.mark.parametrize("supplied", _NON_ASCII_TOKENS)
def test_nonascii_supplied_token_is_denied_not_raised(supplied: str) -> None:
    tokens = {"node-a": "secret-a"}
    assert verify_node_token(tokens, "node-a", supplied) == "node_identity_invalid"


def test_nonascii_registered_token_exact_match_is_accepted() -> None:
    tokens = {"node-a": "s\u00e9cret-a"}
    assert verify_node_token(tokens, "node-a", "s\u00e9cret-a") is None


def test_nonascii_registered_token_wrong_supplied_is_denied() -> None:
    tokens = {"node-a": "s\u00e9cret-a"}
    assert verify_node_token(tokens, "node-a", "secret-a") == "node_identity_invalid"


def test_ascii_correct_token_is_accepted() -> None:
    assert verify_node_token({"node-a": "secret-a"}, "node-a", "secret-a") is None


def test_ascii_wrong_token_is_denied() -> None:
    assert verify_node_token({"node-a": "secret-a"}, "node-a", "secret-b") == "node_identity_invalid"


def test_unknown_agent_is_not_registered() -> None:
    assert verify_node_token({"node-a": "secret-a"}, "unknown", "secret-a") == "node_identity_not_registered"


def test_none_and_empty_supplied_tokens_are_denied() -> None:
    tokens = {"node-a": "secret-a"}
    assert verify_node_token(tokens, "node-a", None) == "node_identity_invalid"
    assert verify_node_token(tokens, "node-a", "") == "node_identity_invalid"


def test_comparison_stays_constant_time_on_bytes() -> None:
    seen: list[tuple[object, object]] = []
    real = verify_node_token.__globals__["hmac"].compare_digest

    def spy(a: object, b: object) -> bool:
        seen.append((a, b))
        return real(a, b)

    with patch("assistx.node_identity.hmac.compare_digest", side_effect=spy):
        assert verify_node_token({"node-a": "secret-a"}, "node-a", "secret-a") is None

    assert len(seen) == 1
    left, right = seen[0]
    assert isinstance(left, bytes) and isinstance(right, bytes)
