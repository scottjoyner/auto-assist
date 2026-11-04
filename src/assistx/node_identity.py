from __future__ import annotations

import hmac


def verify_node_token(
    tokens: dict[str, str],
    agent_id: str | None,
    supplied_token: str | None,
) -> str | None:
    # An operator-provided JSON registry must contain literal string tokens.
    # Coercing numbers/booleans with str() can accidentally make malformed
    # registration entries authenticate (e.g. JSON 123 as header "123").
    expected = tokens.get(str(agent_id or ""))
    if not isinstance(expected, str) or not expected:
        return "node_identity_not_registered"
    if not isinstance(supplied_token, str) or not supplied_token:
        return "node_identity_invalid"
    if not hmac.compare_digest(expected, supplied_token):
        return "node_identity_invalid"
    return None
