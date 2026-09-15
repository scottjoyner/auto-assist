from assistx.repository_graph_report import GraphShadowCase, evaluate_graph_shadow_cases


def test_graph_shadow_report_aggregates_precision_and_recall():
    report = evaluate_graph_shadow_cases(
        [
            GraphShadowCase(
                case_id="one",
                suggested=("src/a.py", "src/b.py"),
                actual=("src/a.py", "src/c.py"),
            ),
            GraphShadowCase(
                case_id="two",
                suggested=("tests/test_a.py",),
                actual=("tests/test_a.py",),
            ),
        ]
    )
    assert report.cases == 2
    assert report.mean_precision == 0.75
    assert report.mean_recall == 0.75
    assert report.perfect_precision_cases == 1
    assert report.zero_hit_cases == 0


def test_graph_shadow_report_tracks_zero_hit_cases():
    report = evaluate_graph_shadow_cases(
        [GraphShadowCase(case_id="miss", suggested=("src/a.py",), actual=("src/b.py",))]
    )
    assert report.zero_hit_cases == 1
    assert report.mean_precision == 0.0
    assert report.mean_recall == 0.0
