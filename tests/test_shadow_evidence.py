import pytest

from assistx.evaluation.shadow_evidence import make_shadow_evidence


def test_shadow_evidence_is_explicitly_non_authoritative():
    evidence = make_shadow_evidence(
        "cache_affinity",
        metrics={"agreed": False, "score": 7},
        source_commit="abc123",
    )
    attrs = evidence.as_trace_attributes()
    assert attrs["authoritative_behavior_changed"] is False
    assert attrs["experiment"] == "cache_affinity"
    assert attrs["metrics"]["score"] == 7


def test_shadow_evidence_requires_names():
    with pytest.raises(ValueError, match="experiment"):
        make_shadow_evidence("", metrics={})
