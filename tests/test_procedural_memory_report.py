from assistx.procedural_memory_report import (
    ProceduralShadowObservation,
    evaluate_procedural_calibration,
)


def test_calibration_report_distinguishes_supported_and_contradicted_rules():
    report = evaluate_procedural_calibration(
        [
            ProceduralShadowObservation("t1", "rule-a", 0.9, True, True),
            ProceduralShadowObservation("t2", "rule-a", 0.8, True, True),
            ProceduralShadowObservation("t3", "rule-b", 0.4, False, False),
            ProceduralShadowObservation("t4", "rule-c", 0.6, True, False),
        ]
    )
    assert report.observations == 4
    assert report.eligible_observations == 3
    assert report.support_rate == 0.5
    assert report.eligible_support_rate == 2 / 3
    assert report.mean_score_supported == 0.85
    assert report.mean_score_contradicted == 0.5


def test_empty_calibration_report_is_stable():
    report = evaluate_procedural_calibration([])
    assert report.observations == 0
    assert report.support_rate == 0.0
    assert report.eligible_support_rate == 0.0
