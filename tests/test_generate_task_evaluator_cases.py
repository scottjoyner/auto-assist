import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "generate_task_evaluator_cases.py"
BASE = (
    REPO
    / "examples"
    / "assistx-inference-policy-experiment"
    / "task-evaluator.cases.jsonl"
)
SUITE = (
    REPO
    / "examples"
    / "assistx-inference-policy-experiment"
    / "task-evaluator.suite.json"
)


def _run(tmp_path: Path, seed: int, count: int) -> dict:
    cases = tmp_path / "cases.jsonl"
    suite = tmp_path / "suite.json"
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base",
            str(BASE),
            "--suite",
            str(SUITE),
            "--output-cases",
            str(cases),
            "--output-suite",
            str(suite),
            "--report",
            str(report),
            "--count-per-kind",
            str(count),
            "--seed",
            str(seed),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return {
        "cases": cases.read_bytes(),
        "suite": suite.read_bytes(),
        "report": json.loads(report.read_text(encoding="utf-8")),
    }


def test_generator_is_deterministic(tmp_path):
    first = _run(tmp_path / "a", seed=20260929, count=2)
    second = _run(tmp_path / "b", seed=20260929, count=2)
    assert first["cases"] == second["cases"]
    assert first["suite"] == second["suite"]
    assert first["report"]["generated_case_ids"] == (
        second["report"]["generated_case_ids"]
    )


def test_generator_validates_ground_truths_and_suite_ids(tmp_path):
    result = _run(tmp_path, seed=20260929, count=2)
    report = result["report"]
    assert report["generated"] == 8  # 2 per kind, 4 kinds
    assert report["validated"] == 8

    rows = [
        json.loads(line)
        for line in result["cases"].decode("utf-8").splitlines()
        if line.strip()
    ]
    suite = json.loads(result["suite"].decode("utf-8"))
    case_ids = [row["case_id"] for row in rows]
    assert len(case_ids) == len(set(case_ids))
    assert set(suite["required_case_ids"]) == set(case_ids)
    for row in rows:
        assert row["evaluation_suite"] == suite["suite_id"]
    kinds = {
        row["acceptance"]["task_evaluator"]["kind"]
        for row in rows
        if "task_evaluator" in row.get("acceptance", {})
    }
    assert kinds == {
        "python_function",
        "structured_json",
        "review_findings",
        "constraint_retention",
    }
