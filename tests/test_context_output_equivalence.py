from types import SimpleNamespace

from assistx.context_output_equivalence import (
    EquivalenceCase,
    run_output_equivalence_case,
    summarize_output_equivalence,
)


def fake_headroom(messages, *, model):
    return SimpleNamespace(
        messages=[dict(message) for message in messages],
        tokens_before=100,
        tokens_after=60,
        tokens_saved=40,
        compression_ratio=0.40,
        transforms_applied=["fixture"],
    )


def test_all_variants_can_be_scored_against_identical_answer_markers():
    case = EquivalenceCase(
        case_id="exact-answer",
        messages=[
            {"role": "user", "content": "Return marker."},
            {"role": "tool", "content": '{\n  "marker": "FLEET-7391"\n}'},
        ],
        required_output_markers=("FLEET-7391",),
    )

    def invoke(messages, variant):
        assert variant in {"raw", "lossless_json", "headroom", "hybrid"}
        return "FLEET-7391"

    result = run_output_equivalence_case(
        case,
        model="fixture-model",
        invoke_fn=invoke,
        headroom_compress_fn=fake_headroom,
    )
    assert result.raw_passed is True
    assert result.all_candidate_variants_match_correctness is True
    assert all(output.passed for output in result.variants)


def test_candidate_regression_is_visible_even_when_raw_passes():
    case = EquivalenceCase(
        case_id="regression",
        messages=[{"role": "user", "content": "Return SAFE-42."}],
        required_output_markers=("SAFE-42",),
    )

    def invoke(_messages, variant):
        return "wrong" if variant == "headroom" else "SAFE-42"

    result = run_output_equivalence_case(
        case,
        model="fixture-model",
        invoke_fn=invoke,
        headroom_compress_fn=fake_headroom,
    )
    headroom = next(v for v in result.variants if v.variant == "headroom")
    assert result.raw_passed is True
    assert headroom.passed is False
    assert result.all_candidate_variants_match_correctness is False

    summary = summarize_output_equivalence([result])
    assert summary["schema_version"] == "assistx.context-output-equivalence.v2"
    assert summary["variants"]["raw"]["output_equivalence_rate"] == 1.0
    assert summary["variants"]["headroom"]["output_equivalence_rate"] == 0.0
    assert summary["variants"]["hybrid"]["output_equivalence_rate"] == 1.0


def test_summary_reports_variant_pass_rates_and_context_sizes():
    case = EquivalenceCase(
        case_id="summary",
        messages=[
            {"role": "user", "content": "Return OK."},
            {"role": "tool", "content": '{\n  "status": "OK"\n}'},
        ],
        required_output_markers=("OK",),
    )
    result = run_output_equivalence_case(
        case,
        model="fixture-model",
        invoke_fn=lambda _messages, _variant: "OK",
        headroom_compress_fn=fake_headroom,
    )
    summary = summarize_output_equivalence([result])
    assert summary["schema_version"] == "assistx.context-output-equivalence.v2"
    assert summary["all_candidate_variants_match_raw_correctness"] is True
    assert summary["variants"]["raw"]["pass_rate"] == 1.0
    assert summary["variants"]["headroom"]["output_equivalence_rate"] == 1.0
    assert summary["variants"]["lossless_json"]["mean_context_chars"] < summary["variants"]["raw"]["mean_context_chars"]
