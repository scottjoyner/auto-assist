from __future__ import annotations

import hmac


def verify_node_token(
    tokens: dict[str, str],
    agent_id: str | None,
    supplied_token: str | None,
) -> str | None:
    expected = str(tokens.get(str(agent_id or "")) or "")
    if not expected:
        return "node_identity_not_registered"
    # hmac.compare_digest() rejects non-ASCII str operands with TypeError.
    # Fleet-node tokens arrive from an HTTP header, so compare UTF-8 bytes
    # instead: constant-time, and total over every Unicode code point.
    if not supplied_token or not hmac.compare_digest(
        expected.encode("utf-8"), supplied_token.encode("utf-8")
    ):
        return "node_identity_invalid"
    return None
