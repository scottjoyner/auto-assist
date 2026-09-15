from assistx.context_optimization_shadow import observe_context_optimization


def test_shadow_measures_json_reduction_but_preserves_raw_execution():
    result = observe_context_optimization('{\n  "a": 1,\n  "b": 2\n}', "application/json")
    assert result.changed is True
    assert result.optimized_chars < result.original_chars
    assert result.reduction_ratio > 0
    assert result.preserve_raw_execution is True


def test_shadow_unknown_text_reports_identity():
    result = observe_context_optimization("keep   exact spacing", "text/plain")
    assert result.changed is False
    assert result.strategy == "identity"
    assert result.reduction_ratio == 0.0
