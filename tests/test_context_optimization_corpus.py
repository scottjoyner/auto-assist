import json
from pathlib import Path

from assistx.context_optimization import optimize_context

CORPUS = Path(__file__).parent / "fixtures" / "context_optimization" / "corpus.json"


def test_context_optimization_corpus_preserves_required_values():
    cases = json.loads(CORPUS.read_text())
    reductions = {}
    for case in cases:
        result = optimize_context(case["content"], case["content_type"])
        reductions[case["id"]] = result.reduction_ratio
        for value in case["must_preserve"]:
            assert value in result.content
        if case["content_type"] == "text/plain":
            assert result.content == case["content"]
    assert reductions["pretty-json"] > 0
    assert reductions["tool-json"] > 0
    assert reductions["plain-instruction"] == 0
