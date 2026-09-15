from assistx.repository_graph_shadow import score_suggestions


def test_shadow_scores_precision_and_recall():
    score = score_suggestions(
        ["src/a.py", "src/b.py", "tests/test_a.py"],
        ["src/a.py", "tests/test_a.py", "src/c.py"],
    )
    assert score.hits == ("src/a.py", "tests/test_a.py")
    assert score.precision == 2 / 3
    assert score.recall == 2 / 3


def test_shadow_deduplicates_suggestions_and_handles_empty_actual():
    score = score_suggestions(["src/a.py", "src/a.py"], [])
    assert score.suggested == ("src/a.py",)
    assert score.precision == 0.0
    assert score.recall == 0.0
