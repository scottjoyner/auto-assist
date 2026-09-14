"""Harness reflect: fail-set extraction, reflect task construction, reflection
extraction and validation — fixtures taken from the real iter4/iter5 fail sets
(BM-007/008/014/018, see the harness version registry)."""

import json

import pytest

from assistx import harness_reflect as hr


@pytest.fixture()
def iter4_run():
    """The 2026-09-13 iter4 fail set (cpm_tb2_143859.json rescore shape)."""
    return {
        "suite_id": "cpm-tb2-bench-v1",
        "harness_id": "cpm-tb2-bench-v1",
        "endpoint": "optiplex:1235",
        "model_key": "minicpm5-2b-iter5",
        "results": [
            {"task_id": "BM-003", "passed": True, "duration_s": 9.0},
            {"task_id": "BM-007", "passed": False,
             "expected": "git log pipeline using real %h formatting, ~1.8KB report",
             "actual": "git pretty format %(h:8s) — invalid specifier",
             "duration_s": 41.0},
            {"task_id": "BM-008", "passed": False,
             "expected": "find command that writes the manifest file",
             "actual": "checked with echo instead of doing the work (check-instead-of-do)",
             "duration_s": 55.0},
            {"task_id": "BM-014", "passed": False,
             "expected": "sieve primes + full report past the 200B gate",
             "actual": "punted on the heavy task; timed out",
             "duration_s": 120.0},
            {"task_id": "BM-018", "passed": False,
             "expected": "gethostbyname + timed curl, output past 100B",
             "actual": "gethostbyaddr crash",
             "duration_s": 18.0},
        ],
    }


def test_fail_set_from_run_keeps_only_failures(iter4_run):
    fails = hr.fail_set_from_run(iter4_run)
    assert [f["task_id"] for f in fails] == ["BM-007", "BM-008", "BM-014", "BM-018"]
    assert all(f["expected"] and f["actual"] for f in fails)


def test_fail_set_rejects_malformed_run():
    with pytest.raises(ValueError):
        hr.fail_set_from_run({"results": "not-a-list"})
    assert hr.fail_set_from_run({"results": [{"task_id": "BM-001", "passed": True}]}) == []


def test_fail_set_is_bounded(iter4_run):
    flood = {
        **iter4_run,
        "results": [
            {"task_id": f"BM-{i:04d}", "passed": False,
             "expected": "x" * 5000, "actual": "y" * 5000}
            for i in range(60)
        ],
    }
    fails = hr.fail_set_from_run(flood)
    assert len(fails) == hr.MAX_FAILS_PER_TASK
    assert all(len(f["expected"]) <= hr.MAX_FIELD_CHARS for f in fails)


def test_run_identity_is_stable_and_sensitive(iter4_run):
    assert hr.run_identity(iter4_run) == hr.run_identity(dict(iter4_run))
    changed = {**iter4_run, "results": iter4_run["results"][:-1]}
    assert hr.run_identity(iter4_run) != hr.run_identity(changed)


def test_build_reflect_task(iter4_run):
    task = hr.build_reflect_task(iter4_run, target_agent_id="optiplex")
    assert task["kind"] == "harness_reflect"
    assert task["required_capabilities"] == ["llm"]
    assert task["target_agent_id"] == "optiplex"
    assert task["idempotency_key"] == f"harness-reflect:{hr.run_identity(iter4_run)}"
    payload = task["payload"]
    assert payload["suite_id"] == "cpm-tb2-bench-v1"
    assert payload["endpoint"] == "optiplex:1235"
    assert len(payload["fail_set"]) == 4
    assert "BM-007" in payload["prompt"]
    assert hr.REFLECTION_CONSTRAINTS in payload["constraints"]
    assert payload["allow_model_load"] is False


def test_build_reflect_task_requires_failures(iter4_run):
    all_pass = {**iter4_run, "results": [
        {"task_id": "BM-003", "passed": True, "duration_s": 9.0},
    ]}
    with pytest.raises(ValueError):
        hr.build_reflect_task(all_pass, target_agent_id="optiplex")


VALID_REFLECTION = {
    "correction_pairs": [
        {"fail_id": "BM-007", "prompt": "git log --pretty=%h ...",
         "completion": "git log --pretty='%h %s' | head -40 > /tmp/app/report.txt"},
        {"fail_id": "BM-018", "prompt": "resolve host and time the curl",
         "completion": "gethostbyname ...; curl -w '%{time_total}' ... 144B output"},
    ],
    "harness_fix_proposals": [
        {"fail_id": "BM-008", "title": "reject check-instead-of-do",
         "patch_summary": "verify written artifact exists, not echoed intent"},
    ],
    "notes": "BM-014 needs a smaller perf task or a longer deadline",
}


def test_extract_reflection_from_fenced_output():
    text = "Here is my reflection:\n```json\n" + json.dumps(VALID_REFLECTION) + "\n```\n"
    assert hr.extract_reflection(text) == VALID_REFLECTION


def test_extract_reflection_from_prose_padded_json():
    text = "Analysis... the fails share root causes:\n" + json.dumps(VALID_REFLECTION) + "\nDone."
    assert hr.extract_reflection(text) == VALID_REFLECTION


def test_extract_reflection_rejects_non_objects():
    assert hr.extract_reflection("no json here at all") is None
    assert hr.extract_reflection("") is None
    assert hr.extract_reflection("[1, 2, 3]") is None


def test_validate_reflection_accepts_valid(iter4_run):
    errors, uncovered = hr.validate_reflection(VALID_REFLECTION, hr.fail_set_from_run(iter4_run))
    assert errors == []
    assert uncovered == ["BM-014"]  # covered via notes only -> not counted


def test_validate_reflection_rejects_unknown_fail_id(iter4_run):
    bad = {
        "correction_pairs": [
            {"fail_id": "BM-999", "prompt": "p", "completion": "c"},
        ],
        "harness_fix_proposals": [],
    }
    errors, _ = hr.validate_reflection(bad, hr.fail_set_from_run(iter4_run))
    assert any("unknown fail_id" in e for e in errors)


def test_validate_reflection_requires_content(iter4_run):
    empty = {"correction_pairs": [], "harness_fix_proposals": []}
    errors, uncovered = hr.validate_reflection(empty, hr.fail_set_from_run(iter4_run))
    assert any("at least one" in e for e in errors)
    assert uncovered == ["BM-007", "BM-008", "BM-014", "BM-018"]

    missing = {"correction_pairs": [{"fail_id": "BM-007"}], "harness_fix_proposals": []}
    errors, _ = hr.validate_reflection(missing, hr.fail_set_from_run(iter4_run))
    assert any("missing completion" in e for e in errors)


def test_validate_reflection_rejects_oversized_completion(iter4_run):
    big = {
        "correction_pairs": [
            {"fail_id": "BM-007", "prompt": "p", "completion": "x" * 5000},
        ],
        "harness_fix_proposals": [],
    }
    errors, _ = hr.validate_reflection(big, hr.fail_set_from_run(iter4_run))
    assert any("exceeds" in e for e in errors)


def test_validate_reflection_non_object(iter4_run):
    errors, uncovered = hr.validate_reflection("nope", hr.fail_set_from_run(iter4_run))
    assert errors == ["reflection must be a JSON object"]
    assert len(uncovered) == 4
