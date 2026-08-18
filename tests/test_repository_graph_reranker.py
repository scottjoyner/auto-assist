from assistx.repository_graph import normalize_graphify_graph
from assistx.repository_graph_reranker import paths, rank_graph_files


def _projection():
    return normalize_graphify_graph(
        {
            "nodes": [
                {"id": "a", "label": "seed", "source_file": "src/assistx/api.py"},
                {"id": "b", "label": "client", "source_file": "src/assistx/llm/client.py"},
                {"id": "c", "label": "test", "source_file": "tests/test_api.py"},
                {"id": "d", "label": "config", "source_file": ".github/workflows/ci.yml"},
                {"id": "e", "label": "neo", "source_file": "src/assistx/neo4j_client.py"},
                {"id": "f", "label": "docs", "source_file": "docs/api.md"},
            ],
            "links": [
                {"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "a", "target": "c", "relation": "references", "confidence": "EXTRACTED"},
                {"source": "b", "target": "e", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "c", "target": "d", "relation": "references", "confidence": "EXTRACTED"},
                {"source": "b", "target": "f", "relation": "rationale_for", "confidence": "EXTRACTED"},
            ],
        },
        repository="owner/repo",
        commit_sha="abc123",
    )


def test_depth1_high_signal_candidates_rank_before_depth2_by_default():
    ranked = rank_graph_files(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="fix api tests and llm client behavior",
        max_depth=2,
        limit=5,
    )
    assert ranked[0].depth == 1
    assert ranked[0].path in {"src/assistx/llm/client.py", "tests/test_api.py"}
    assert "src/assistx/neo4j_client.py" in paths(ranked)


def test_task_terms_lift_relevant_test_into_primary_shortlist():
    ranked = rank_graph_files(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="pytest api coverage failing test",
        max_depth=2,
        limit=3,
    )
    by_path = {candidate.path: candidate for candidate in ranked}
    assert "tests/test_api.py" in by_path
    assert by_path["tests/test_api.py"].depth == 1
    assert by_path["tests/test_api.py"].role_affinity == 1.0


def test_historical_evidence_can_lift_depth2_without_unbounding_context():
    ranked = rank_graph_files(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="neo4j regression",
        max_depth=2,
        limit=2,
        historical_change_scores={"src/assistx/neo4j_client.py": 1.0},
    )
    assert len(ranked) == 2
    assert "src/assistx/neo4j_client.py" in paths(ranked)


def test_unknown_seed_is_empty_and_invalid_limits_are_rejected():
    assert rank_graph_files(
        _projection(), seed_file="missing.py", task_text="anything"
    ) == ()
