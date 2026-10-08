"""Security regression tests for the offline trace acceptance evidence collector."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
from xml.etree import ElementTree

import pytest

# The repository exposes src/ in pytest.ini, not scripts/. Import the standalone
# collector by its absolute path to avoid relying on the runner's sys.path.
COLLECTOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "trace_acceptance_report.py"
SPEC = importlib.util.spec_from_file_location("trace_acceptance_report", COLLECTOR_PATH)
assert SPEC is not None and SPEC.loader is not None
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)
REQUIRED = COLLECTOR.REQUIRED
create_report = COLLECTOR.create_report
validate_report = COLLECTOR.validate_report

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def write_junit(path: Path, *, missing=(), overrides=None, include_unknown=False):
    root = ElementTree.Element("testsuites")
    suite = ElementTree.SubElement(root, "testsuite", name="pytest")
    overrides = overrides or {}
    for key in sorted({name for group in REQUIRED.values() for name in group} - set(missing)):
        module, function = key.split("::")
        minimum = COLLECTOR.MIN_CASES.get(key, 1)
        for i in range(minimum):
            name = function if minimum == 1 else function + f"[case-{i}]"
            testcase = ElementTree.SubElement(
                suite, "testcase", classname="tests." + module, name=name
            )
            status = overrides.get(key)
            if status:
                ElementTree.SubElement(testcase, status, message="synthetic test result")
    if include_unknown:
        ElementTree.SubElement(
            suite, "testcase", classname="untrusted.other", name="test_simulated_deployed_pass"
        )
    path.write_bytes(ElementTree.tostring(root, encoding="utf-8"))


def collect(path):
    return create_report(path, head=SHA_A, base=SHA_B, tested=SHA_C, observed_at="2026-10-08T00:00:00+00:00")


def test_complete_fixture_matrix_is_still_not_production_authority(tmp_path):
    xml = tmp_path / "results.xml"
    write_junit(xml)
    report = collect(xml)
    validate_report(report)
    assert report["promotion_eligible"] is False
    assert report["production_dispatch_authorized"] is False
    assert report["checkout_matches_pr_head"] is False
    assert report["junit_sha256"] == hashlib.sha256(xml.read_bytes()).hexdigest()
    assert report["gates"]["ci_baseline"]["fixture_status"] == "blocked"
    for gate, state in report["gates"].items():
        assert state["activation_status"] == "blocked", gate
        if gate != "ci_baseline":
            assert state["fixture_status"] == "fixture_pass", gate
            assert all(c["provenance"] == "unit_fixture" for c in state["checks"])


@pytest.mark.parametrize("status", ["failure", "error", "skipped"])
def test_failed_errored_and_skipped_fixture_cannot_be_passed(tmp_path, status):
    xml = tmp_path / "results.xml"
    identity = REQUIRED["lease_freshness"][0]
    write_junit(xml, overrides={identity: status})
    report = collect(xml)
    assert report["gates"]["lease_freshness"]["fixture_status"] == "blocked"
    check = report["gates"]["lease_freshness"]["checks"][0]
    assert check["reason"] == "failed_skipped_or_errored"


def test_missing_required_test_blocks_all_affected_gates(tmp_path):
    xml = tmp_path / "results.xml"
    identity = REQUIRED["lease_freshness"][2]
    write_junit(xml, missing=[identity])
    report = collect(xml)
    assert report["gates"]["lease_freshness"]["fixture_status"] == "blocked"
    assert report["gates"]["revocation_supersession"]["fixture_status"] == "blocked"
    assert report["gates"]["lease_freshness"]["checks"][2]["reason"] == "missing_test"


def test_unknown_extra_cases_cannot_create_deployed_evidence(tmp_path):
    xml = tmp_path / "results.xml"
    write_junit(xml, include_unknown=True)
    report = collect(xml)
    assert report["promotion_eligible"] is False
    assert "deployed_pass" not in str(report)


def test_missing_junit_fails_closed(tmp_path):
    report = collect(tmp_path / "missing.xml")
    validate_report(report)
    assert "junit_missing" in report["collector_errors"]
    assert all(g["fixture_status"] == "blocked" for g in report["gates"].values())


def test_unparseable_junit_fails_closed(tmp_path):
    xml = tmp_path / "results.xml"
    xml.write_text("<testsuites><broken", encoding="utf-8")
    report = collect(xml)
    validate_report(report)
    assert "junit_invalid:ParseError" in report["collector_errors"]
    assert all(g["activation_status"] == "blocked" for g in report["gates"].values())


def test_invalid_source_revision_invalidates_all_fixtures(tmp_path):
    xml = tmp_path / "results.xml"
    write_junit(xml)
    report = create_report(xml, head="", base=SHA_B, tested=SHA_C)
    validate_report(report)
    assert report["pr_head_sha"] is None
    assert "pr_head_sha_missing_or_invalid" in report["collector_errors"]
    assert all(g["fixture_status"] == "blocked" for g in report["gates"].values())


@pytest.mark.parametrize("change", [
    ("promotion_eligible", True),
    ("production_dispatch_authorized", True),
])
def test_mutating_promotion_flags_is_rejected(tmp_path, change):
    xml = tmp_path / "results.xml"
    write_junit(xml)
    report = collect(xml)
    report[change[0]] = change[1]
    with pytest.raises(ValueError, match="unsafe_"):
        validate_report(report)


def test_mutating_gate_to_deployed_pass_is_rejected(tmp_path):
    xml = tmp_path / "results.xml"
    write_junit(xml)
    report = collect(xml)
    altered = copy.deepcopy(report)
    altered["gates"]["production_api"]["activation_status"] = "deployed_pass"
    with pytest.raises(ValueError, match="unsafe_gate"):
        validate_report(altered)


def test_claimed_deployed_evidence_is_rejected(tmp_path):
    xml = tmp_path / "results.xml"
    write_junit(xml)
    report = collect(xml)
    report["gates"]["key_custody"]["checks"][0]["provenance"] = "deployed_observation"
    with pytest.raises(ValueError, match="unsafe_evidence"):
        validate_report(report)


def test_partial_parameterized_denial_matrix_does_not_count_as_pass(tmp_path):
    xml = tmp_path / "results.xml"
    write_junit(xml)
    tree = ElementTree.parse(xml)
    key = REQUIRED["revocation_supersession"][0]
    module, name = key.split("::")
    suite = next(tree.getroot().iter("testsuite"))
    matching = [
        case for case in suite.findall("testcase")
        if case.get("classname") == "tests." + module
        and case.get("name", "").startswith(name + "[")
    ]
    assert len(matching) == 17
    for case in matching[1:]:
        suite.remove(case)
    tree.write(xml, encoding="utf-8")
    report = collect(xml)
    check = report["gates"]["revocation_supersession"]["checks"][0]
    assert check["result"] == "blocked"
    assert check["case_count"] == 1
    assert check["required_case_count"] == 17
    assert check["reason"] == "insufficient_case_coverage"
