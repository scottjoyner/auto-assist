import json

from assistx.context_optimization import compact_json_text, optimize_context


def test_json_minification_preserves_values_and_reduces_pretty_payload():
    source = '{\n  "alpha": 1,\n  "nested": {\n    "beta": [1, 2, 3]\n  }\n}'
    result = compact_json_text(source)
    assert result.changed is True
    assert result.strategy == "json_minify"
    assert result.optimized_chars < result.original_chars
    assert json.loads(result.content) == json.loads(source)


def test_invalid_json_is_identity():
    source = "not-json: 123"
    result = compact_json_text(source)
    assert result.changed is False
    assert result.content == source
    assert result.strategy == "identity"


def test_unknown_content_type_is_not_modified():
    source = "exact user instruction   keep spacing"
    result = optimize_context(source, "text/plain")
    assert result.changed is False
    assert result.content == source


def test_empty_input_has_zero_reduction_ratio():
    result = optimize_context("", "text/plain")
    assert result.reduction_ratio == 0.0
