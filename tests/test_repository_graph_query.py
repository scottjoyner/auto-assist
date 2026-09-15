from assistx.repository_graph import normalize_graphify_graph
from assistx.repository_graph_query import incoming_impact, neighborhood


def _projection():
    return normalize_graphify_graph(
        {
            "nodes": [
                {"id": "A", "label": "A"},
                {"id": "B", "label": "B"},
                {"id": "C", "label": "C"},
                {"id": "D", "label": "D"},
            ],
            "links": [
                {"source": "A", "target": "B", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "C", "target": "B", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "D", "target": "C", "relation": "calls", "confidence": "EXTRACTED"},
            ],
        },
        repository="owner/repo",
        commit_sha="abc123",
    )


def test_neighborhood_returns_bounded_context():
    projection = _projection()
    node_b = "owner/repo@abc123:B"
    labels = [node["label"] for node in neighborhood(projection, node_b, depth=1)]
    assert labels == ["B", "A", "C"]


def test_incoming_impact_finds_transitive_dependents():
    projection = _projection()
    node_b = "owner/repo@abc123:B"
    impacted = incoming_impact(projection, node_b, depth=2)
    assert impacted == (
        "owner/repo@abc123:A",
        "owner/repo@abc123:C",
        "owner/repo@abc123:D",
    )


def test_unknown_node_has_empty_neighborhood():
    assert neighborhood(_projection(), "missing", depth=2) == ()
