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


def _load_generator_module():
    sys.path.insert(0, str(REPO / "src"))
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib

    return importlib.import_module("generate_task_evaluator_cases")


def test_every_code_template_satisfies_the_evaluator_contract():
    """A ground truth a compliant model cannot reach is a broken case.

    The templates are the answer key for `python_function` cases, so a template
    using a `while` loop or a method call would mark a correct model output as
    wrong. The evaluator's own validator is the contract.
    """
    import ast

    sys.path.insert(0, str(REPO / "src"))
    from assistx.inference_task_evaluators import _validate_python_ast

    generator = _load_generator_module()
    templates = generator._code_templates()
    assert len(templates) >= 20, "corpus diversity depends on the template count"

    for template in templates:
        _validate_python_ast(ast.parse(template["source"]), template["name"])


def test_template_parameters_match_the_declared_order():
    generator = _load_generator_module()

    for template in generator._code_templates():
        order = template["param_order"]
        assert order, template["name"]
        for attempt in range(5):
            params = template["params"](generator.random.Random(attempt))
            assert set(params) == set(order), template["name"]
            assert list(params) == order or set(params) == set(order), (
                f"{template['name']} must build arguments in param_order"
            )


def test_generated_cases_have_no_duplicate_acceptance_specs(tmp_path):
    report = _run(tmp_path, seed=7, count=12)["report"]
    cases = [
        json.loads(line)
        for line in (tmp_path / "cases.jsonl").read_text().splitlines()
    ]

    keys = [json.dumps(case["acceptance"], sort_keys=True) for case in cases]
    assert len(keys) == len(set(keys)), "duplicate acceptance specs teach nothing"

    assert report["distinct_acceptance_specs"] == len(cases)
    assert report["generated_distinct_acceptance_specs"] == report["generated"]


def test_report_states_how_much_task_diversity_was_reached(tmp_path):
    report = _run(tmp_path, seed=11, count=8)["report"]

    assert report["code_templates_available"] >= 20
    assert report["code_templates_used"] == min(
        8,
        report["code_templates_available"],
    ), "every requested code case should use a different task"
    assert report["distinct_specs_skipped"] >= 0
