from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "scripts" / "render-kipnerter-agent-auto-report.py"
PUBLISH = ROOT / "scripts" / "publish-kipnerter-agent-auto-report.sh"


def _render(
    evidence: Path,
    *,
    result: str = "PASS",
    exit_code: int = 0,
    repo_validation_result: str = "PASS_WITH_NAMED_BASELINE_EXCEPTIONS",
    repo_baseline_exceptions: str = (
        "test_fleet_dashboard_html_includes_inference_section; "
        "test_fleet_dashboard_html_has_all_sections"
    ),
) -> dict:
    (evidence / "tailscale-serve-before.json").write_text('{"serve":"same"}\n')
    (evidence / "tailscale-serve-after.json").write_text('{"serve":"same"}\n')
    (evidence / "whoami-status.txt").write_text("200\n")
    (evidence / "spoof-negative-status.txt").write_text("401\n")
    (evidence / "agent-auto-status.txt").write_text("200\n")
    (evidence / "agent-auto-response.headers").write_text(
        "HTTP/2 200\r\nX-Kipnerter-Agent-Executor: hermes\r\n"
    )
    subprocess.run(
        [
            "python",
            str(RENDER),
            "--evidence-dir",
            str(evidence),
            "--result",
            result,
            "--stage",
            "complete" if result == "PASS" else "agent_auto_smoke",
            "--source-sha",
            "a" * 40,
            "--gateway",
            "https://x1-370.example.ts.net:8443",
            "--exit-code",
            str(exit_code),
            "--failure-reason",
            "" if result == "PASS" else "bounded smoke failed",
            "--timestamp-utc",
            "20260923T204500Z",
            "--repo-validation-result",
            repo_validation_result,
            "--repo-baseline-exceptions",
            repo_baseline_exceptions,
            "--repo-validation-run-url",
            "https://github.com/scottjoyner/auto-assist/actions/runs/123",
        ],
        check=True,
    )
    return json.loads((evidence / "validation-report.json").read_text())


def test_renderer_allows_healthy_claim_only_for_live_pass(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    report = _render(evidence)

    assert report["result"] == "PASS"
    assert report["healthy_claim_allowed"] is True
    assert report["serve_topology_unchanged"] is True
    assert report["tailnet_whoami_http"] == "200"
    assert report["executor_spoof_http"] == "401"
    assert report["agent_auto_http"] == "200"
    assert report["agent_executor"] == "hermes"
    assert report["authority_widening_performed_by_verifier"] is False
    assert report["repo_validation_result"] == "PASS_WITH_NAMED_BASELINE_EXCEPTIONS"
    assert "test_fleet_dashboard_html_includes_inference_section" in str(
        report["repo_baseline_exceptions"]
    )
    knowledge = (evidence / "knowledge-report.md").read_text()
    assert "Agent Auto live path verified" in knowledge


def test_renderer_live_pass_without_repo_validation_is_not_healthy(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    report = _render(
        evidence,
        result="PASS",
        exit_code=0,
        repo_validation_result="UNRECORDED",
        repo_baseline_exceptions="",
    )

    assert report["result"] == "PASS"
    assert report["healthy_claim_allowed"] is False
    assert "No healthy Agent Auto claim is permitted" in (
        evidence / "knowledge-report.md"
    ).read_text()


def test_renderer_never_promotes_failed_attempt(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    report = _render(evidence, result="FAIL", exit_code=1)

    assert report["healthy_claim_allowed"] is False
    assert report["failure_reason"] == "bounded smoke failed"
    assert "No healthy Agent Auto claim is permitted" in (
        evidence / "knowledge-report.md"
    ).read_text()


def test_publisher_writes_only_bounded_knowledge_project_record(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    _render(evidence)

    knowledge = tmp_path / "knowledge"
    project = knowledge / "20-Projects" / "kipnerter-ios"
    project.mkdir(parents=True)
    log = project / "EXECUTION-LOG-2026-09-09-RC2-GATEWAY.md"
    log.write_text("# Existing gateway log\n")

    env = os.environ.copy()
    env["KNOWLEDGE_ROOT"] = str(knowledge)
    subprocess.run(["bash", str(PUBLISH), str(evidence)], env=env, check=True)

    target = project / "validation" / "AGENT-AUTO-LIVE-20260923T204500Z.md"
    assert target.is_file()
    assert "Result: **PASS**" in target.read_text()
    log_text = log.read_text()
    assert "Agent Auto live validation checkpoint" in log_text
    assert "only a live `PASS` permits the Agent Auto healthy claim" in log_text
    assert (evidence / "knowledge-published-path.txt").read_text().strip() == str(target)


def test_publisher_and_renderer_have_valid_syntax() -> None:
    subprocess.run(["bash", "-n", str(PUBLISH)], check=True)
    subprocess.run(["python", "-m", "py_compile", str(RENDER)], check=True)
