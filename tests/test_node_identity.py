from assistx.node_identity import verify_node_token


def test_node_identity_is_bound_to_agent_id():
    tokens = {"node-a": "secret-a", "node-b": "secret-b"}

    assert verify_node_token(tokens, "node-a", "secret-a") is None
    assert verify_node_token(tokens, "node-a", "secret-b") == "node_identity_invalid"
    assert verify_node_token(tokens, "unknown", "secret-a") == "node_identity_not_registered"
    assert verify_node_token(tokens, "node-a", None) == "node_identity_invalid"
