from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read_text(evidence: Path, name: str) -> str | None:
    path = evidence / name
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _agent_executor(evidence: Path) -> str | None:
    headers = (_read_text(evidence, "agent-auto-response.headers") or "").splitlines()
    for line in headers:
        if line.lower().startswith("x-kipnerter-agent-executor:"):
            return line.split(":", 1)[1].strip().lower()
    return None


def render_report(
    *,
    evidence_dir: Path,
    result: str,
    stage: str,
    source_sha: str,
    gateway: str,
    exit_code: int,
    failure_reason: str,
    timestamp_utc: str,
    repo_validation_result: str,
    repo_baseline_exceptions: str,
    repo_validation_run_url: str,
) -> dict[str, object]:
    before = evidence_dir / "tailscale-serve-before.json"
    after = evidence_dir / "tailscale-serve-after.json"
    serve_unchanged: bool | None = None
    if before.exists() and after.exists():
        serve_unchanged = before.read_bytes() == after.read_bytes()

    failure_reason = failure_reason.strip()
    if exit_code and not failure_reason:
        failure_reason = f"command failed during {stage}"

    executor = _agent_executor(evidence_dir)
    repo_validation_accepted = repo_validation_result in {
        "PASS",
        "PASS_WITH_NAMED_BASELINE_EXCEPTIONS",
    }
    named_exception_ok = (
        repo_validation_result != "PASS_WITH_NAMED_BASELINE_EXCEPTIONS"
        or bool(repo_baseline_exceptions.strip())
    )
    healthy = (
        result == "PASS"
        and exit_code == 0
        and repo_validation_accepted
        and named_exception_ok
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "timestamp_utc": timestamp_utc,
        "result": result,
        "exit_code": exit_code,
        "last_stage": stage,
        "failure_reason": failure_reason or None,
        "source_sha": source_sha,
        "gateway": gateway,
        "repo_validation_result": repo_validation_result,
        "repo_baseline_exceptions": repo_baseline_exceptions.strip() or None,
        "repo_validation_run_url": repo_validation_run_url.strip() or None,
        "serve_topology_unchanged": serve_unchanged,
        "tailnet_whoami_http": _read_text(evidence_dir, "whoami-status.txt"),
        "executor_spoof_http": _read_text(evidence_dir, "spoof-negative-status.txt"),
        "agent_auto_http": _read_text(evidence_dir, "agent-auto-status.txt"),
        "agent_executor": executor,
        "healthy_claim_allowed": healthy,
        "authority_widening_performed_by_verifier": False,
        "knowledge_record_relative_dir": "20-Projects/kipnerter-ios/validation",
        "canonical_gateway_log": (
            "20-Projects/kipnerter-ios/"
            "EXECUTION-LOG-2026-09-09-RC2-GATEWAY.md"
        ),
    }
    (evidence_dir / "validation-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if healthy:
        claim = (
            f"Agent Auto live path verified at {source_sha} by exact-source API "
            "recreate, unchanged Serve topology, Tailnet identity, executor-spoof "
            "rejection, and one HTTP 200 Hermes-backed Agent Auto smoke."
        )
    else:
        claim = "No healthy Agent Auto claim is permitted from this attempt."

    md = f"""# Kipnerter Agent Auto live validation — {timestamp_utc}

- Result: **{result}**
- Exact auto-assist SHA: `{source_sha}`
- Last stage: `{stage}`
- Exit code: `{exit_code}`
- Failure reason: {failure_reason or "none"}
- Evidence directory: `{evidence_dir}`
- Serve topology unchanged: `{serve_unchanged}`
- Tailnet whoami HTTP: `{report["tailnet_whoami_http"]}`
- Executor-spoof HTTP: `{report["executor_spoof_http"]}`
- Agent Auto HTTP: `{report["agent_auto_http"]}`
- Agent executor: `{executor}`
- Repository validation: `{repo_validation_result}`
- Named baseline exceptions: {repo_baseline_exceptions or "none"}
- Repository validation run: {repo_validation_run_url or "not recorded"}
- Authority widening performed by verifier: **no**

{claim}

Repository CI and named baseline exceptions must be recorded separately. This
live report does not waive a repository failure and must not be used to report
the separate runtime-directory/model-handle slice as accepted.
"""
    (evidence_dir / "knowledge-report.md").write_text(md, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--result", required=True, choices=("BLOCKED", "FAIL", "PASS"))
    parser.add_argument("--stage", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--exit-code", required=True, type=int)
    parser.add_argument("--failure-reason", default="")
    parser.add_argument("--timestamp-utc", required=True)
    parser.add_argument(
        "--repo-validation-result",
        required=True,
        choices=("PASS", "PASS_WITH_NAMED_BASELINE_EXCEPTIONS", "FAIL", "UNRECORDED"),
    )
    parser.add_argument("--repo-baseline-exceptions", default="")
    parser.add_argument("--repo-validation-run-url", default="")
    args = parser.parse_args()

    if (
        args.repo_validation_result == "PASS_WITH_NAMED_BASELINE_EXCEPTIONS"
        and not args.repo_baseline_exceptions.strip()
    ):
        parser.error(
            "--repo-baseline-exceptions is required for "
            "PASS_WITH_NAMED_BASELINE_EXCEPTIONS"
        )

    render_report(
        evidence_dir=args.evidence_dir,
        result=args.result,
        stage=args.stage,
        source_sha=args.source_sha,
        gateway=args.gateway,
        exit_code=args.exit_code,
        failure_reason=args.failure_reason,
        timestamp_utc=args.timestamp_utc,
        repo_validation_result=args.repo_validation_result,
        repo_baseline_exceptions=args.repo_baseline_exceptions,
        repo_validation_run_url=args.repo_validation_run_url,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
