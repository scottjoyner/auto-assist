import pytest

from assistx.repository_graph import normalize_graphify_graph


def _graph():
    return {
        "nodes": [
            {"id": "A", "label": "alpha", "file_type": "code", "source_file": "a.py"},
            {"id": "B", "label": "beta", "file_type": "code", "source_file": "b.py"},
        ],
        "links": [
            {
                "source": "A",
                "target": "B",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "source_file": "a.py",
            }
        ],
    }


def test_graphify_projection_is_namespaced_and_commit_pinned():
    projection = normalize_graphify_graph(
        _graph(), repository="scottjoyner/auto-assist", commit_sha="abc123"
    )
    assert projection.repository == "scottjoyner/auto-assist"
    assert projection.commit_sha == "abc123"
    assert projection.nodes[0]["id"] == "scottjoyner/auto-assist@abc123:A"
    assert projection.edges[0]["source"] == "scottjoyner/auto-assist@abc123:A"
    assert projection.edges[0]["target"] == "scottjoyner/auto-assist@abc123:B"
    assert projection.edges[0]["confidence"] == "EXTRACTED"


def test_networkx_edges_key_is_supported_too():
    graph = _graph()
    graph["edges"] = graph.pop("links")
    projection = normalize_graphify_graph(
        graph, repository="owner/repo", commit_sha="deadbeef"
    )
    assert len(projection.edges) == 1


def test_unknown_edge_node_is_rejected():
    graph = _graph()
    graph["links"][0]["target"] = "missing"
    with pytest.raises(ValueError, match="unknown node"):
        normalize_graphify_graph(graph, repository="owner/repo", commit_sha="deadbeef")


def test_duplicate_node_id_is_rejected():
    graph = _graph()
    graph["nodes"].append(dict(graph["nodes"][0]))
    with pytest.raises(ValueError, match="duplicate Graphify node id"):
        normalize_graphify_graph(graph, repository="owner/repo", commit_sha="deadbeef")


def test_invalid_confidence_is_rejected():
    graph = _graph()
    graph["links"][0]["confidence"] = "MAGIC"
    with pytest.raises(ValueError, match="unsupported edge confidence"):
        normalize_graphify_graph(graph, repository="owner/repo", commit_sha="deadbeef")
