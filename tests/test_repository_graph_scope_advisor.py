from assistx.repository_graph import normalize_graphify_graph
from assistx.repository_graph_scope_advisor import advise_scope


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


def test_seed_is_always_first_and_total_scope_is_bounded():
    advice = advise_scope(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="fix api tests and neo4j regression",
        max_files=4,
        expansion_budget=1,
    )
    assert advice.candidate_paths[0] == "src/assistx/api.py"
    assert len(advice.candidate_paths) <= 4
    assert len(advice.expansion_paths) <= 1


def test_zero_expansion_budget_is_precision_first():
    advice = advise_scope(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="api client fix",
        max_files=3,
        expansion_budget=0,
    )
    assert advice.expansion_paths == ()
    assert all(path in {"src/assistx/llm/client.py", "tests/test_api.py"} for path in advice.primary_paths)


def test_single_file_contract_never_expands_scope():
    advice = advise_scope(
        _projection(),
        seed_file="src/assistx/api.py",
        task_text="tiny fix",
        max_files=1,
        expansion_budget=2,
    )
    assert advice.candidate_paths == ("src/assistx/api.py",)
    assert advice.primary_paths == ()
    assert advice.expansion_paths == ()
