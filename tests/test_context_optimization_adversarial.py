import json
from pathlib import Path

from assistx.context_optimization import optimize_context

CORPUS = Path(__file__).parent / "fixtures" / "context_optimization" / "adversarial_corpus.json"


def test_adversarial_context_fidelity():
    cases = json.loads(CORPUS.read_text())
    for case in cases:
        result = optimize_context(case["content"], case["content_type"])
        for value in case["must_preserve"]:
            assert value in result.content
        if case["content_type"] == "text/plain":
            assert result.content == case["content"]
        else:
            assert json.loads(result.content) == json.loads(case["content"])
