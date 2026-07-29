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
    if not supplied_token or not hmac.compare_digest(expected, supplied_token):
        return "node_identity_invalid"
    return None
