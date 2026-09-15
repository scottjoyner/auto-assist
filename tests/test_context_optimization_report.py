from assistx.context_optimization_report import ContextCase, evaluate_context_corpus


def test_corpus_report_tracks_reduction_and_fidelity():
    report = evaluate_context_corpus(
        [
            ContextCase(
                case_id="json",
                content_type="application/json",
                content='{\n  "a": 1,\n  "b": 2\n}',
                must_preserve=("1", "2"),
            ),
            ContextCase(
                case_id="plain",
                content_type="text/plain",
                content="keep   exact spacing",
                must_preserve=("keep   exact spacing",),
            ),
        ]
    )
    assert report.cases == 2
    assert report.changed_cases == 1
    assert report.fidelity_failures == 0
    assert report.mean_reduction_ratio > 0
    assert report.results[0].fidelity_passed is True
    assert report.results[1].reduction_ratio == 0.0


def test_corpus_report_exposes_fidelity_failure():
    report = evaluate_context_corpus(
        [
            ContextCase(
                case_id="bad-expectation",
                content_type="application/json",
                content='{"a":1}',
                must_preserve=("missing-value",),
            )
        ]
    )
    assert report.fidelity_failures == 1
    assert report.results[0].fidelity_passed is False
