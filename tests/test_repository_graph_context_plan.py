from assistx.repository_graph import normalize_graphify_graph
from assistx.repository_graph_context_plan import build_graph_context_plan


def _projection():
    return normalize_graphify_graph(
        {
            "nodes": [
                {"id": "a", "source_file": "src/assistx/api.py"},
                {"id": "b", "source_file": "src/assistx/llm/client.py"},
                {"id": "c", "source_file": "tests/test_api.py"},
                {"id": "d", "source_file": "src/assistx/neo4j_client.py"},
                {"id": "e", "source_file": "tests/test_neo4j_client.py"},
            ],
            "links": [
                {"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"},
                {"source": "a", "target": "c", "relation": "references", "confidence": "EXTRACTED"},
                {"source": "b", "target": "d", "relation": "imports", "confidence": "EXTRACTED"},
                {"source": "c", "target": "e", "relation": "references", "confidence": "EXTRACTED"},
            ],
        },
        repository="owner/repo",
        commit_sha="abc123",
    )


def test_context_plan_keeps_depth1_primary_and_depth2_expansion_separate():
    plan = build_graph_context_plan(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="fix api tests and neo4j regression",
        primary_limit=2,
        expansion_limit=2,
    )
    assert len(plan.primary) == 2
    assert all(candidate.depth == 1 for candidate in plan.primary)
    assert all(candidate.depth == 2 for candidate in plan.expansion)
    assert not ({candidate.path for candidate in plan.primary} & {candidate.path for candidate in plan.expansion})
    assert len(plan.paths) <= 4


def test_context_plan_supports_precision_only_mode():
    plan = build_graph_context_plan(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="api change",
        primary_limit=1,
        expansion_limit=0,
    )
    assert len(plan.primary) == 1
    assert plan.expansion == ()


def test_context_plan_can_be_disabled_without_graph_mutation():
    plan = build_graph_context_plan(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="anything",
        primary_limit=0,
        expansion_limit=0,
    )
    assert plan.primary == ()
    assert plan.expansion == ()
    assert plan.paths == ()
