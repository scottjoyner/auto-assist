from assistx.repository_graph import normalize_graphify_graph
from assistx.repository_graph_freshness import check_projection_freshness


def _projection():
    return normalize_graphify_graph(
        {"nodes": [{"id": "A", "label": "A"}], "links": []},
        repository="owner/repo",
        commit_sha="commit-a",
    )


def test_matching_repo_and_commit_is_fresh():
    state = check_projection_freshness(
        _projection(), repository="owner/repo", current_commit_sha="commit-a"
    )
    assert state.fresh is True


def test_new_head_marks_projection_stale():
    state = check_projection_freshness(
        _projection(), repository="owner/repo", current_commit_sha="commit-b"
    )
    assert state.fresh is False
    assert state.commit_matches is False


def test_wrong_repository_marks_projection_stale():
    state = check_projection_freshness(
        _projection(), repository="other/repo", current_commit_sha="commit-a"
    )
    assert state.fresh is False
    assert state.repository_matches is False
