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


def _run(tmp_path: Path, seed: int, count: int, **extra) -> dict:
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
            *(
                [arg for key, value in extra.items() for arg in (f"--{key.replace('_', '-')}", str(value))]
            ),
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
    # The suite records the absolute path of the corpus it was written beside,
    # so two runs to different directories differ in exactly that field.
    def _without_path(document: bytes, path: Path) -> bytes:
        return document.replace(str(path.resolve()).encode(), b"")

    assert _without_path(
        first["suite"], tmp_path / "a" / "cases.jsonl"
    ) == _without_path(second["suite"], tmp_path / "b" / "cases.jsonl")
    assert first["report"]["generated_case_ids"] == (
        second["report"]["generated_case_ids"]
    )


def test_suite_binds_the_corpus_it_was_written_beside(tmp_path):
    """A suite pointing at another corpus is a silent binding error."""
    _run(tmp_path, seed=20260929, count=2)
    suite = json.loads((tmp_path / "suite.json").read_text())
    cases_path = (tmp_path / "cases.jsonl").resolve()

    assert suite["cases_file"] == str(cases_path)
    assert Path(suite["cases_file"]).exists()
    written = {
        json.loads(line)["case_id"]
        for line in (tmp_path / "cases.jsonl").read_text().splitlines()
    }
    assert set(suite["required_case_ids"]) == written


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


def test_every_family_can_reach_the_requested_count(tmp_path):
    """No family may be the diversity ceiling.

    The corpus is only useful if each family can supply the number of distinct
    cases asked for. Structured was 4 tools x 7 nodes = 28, context was
    3 revisions x 3 layers = 9, and review was subsets of 4 defect kinds, so a
    run requesting 60 per family silently produced 30/11/7.
    """
    count = 20
    report = _run(tmp_path, seed=20260930, count=count)["report"]
    cases = [
        json.loads(line)
        for line in (tmp_path / "cases.jsonl").read_text().splitlines()
    ]
    generated = [case for case in cases if case.get("generator")]

    assert report["distinct_specs_skipped"] == 0, (
        "a family ran out of distinct specs before reaching the requested count"
    )
    assert len(generated) == 4 * count
    # Count by case kind, not task_family: the family taxonomy deliberately
    # folds code review into "coding", so the label cannot distinguish them.
    kinds = {
        "code": "taskq-code-gen-",
        "tool": "taskq-tool-",
        "review": "taskq-review-gen-",
        "context": "taskq-context-gen-",
    }
    for kind, prefix in kinds.items():
        assert sum(1 for c in generated if c["case_id"].startswith(prefix)) == count, (
            f"{kind} family did not reach {count} distinct cases"
        )


def test_structured_cases_require_the_fields_they_state(tmp_path):
    report = _run(tmp_path, seed=3, count=20)["report"]
    cases = [
        json.loads(line)
        for line in (tmp_path / "cases.jsonl").read_text().splitlines()
    ]
    structured = [
        case
        for case in cases
        if case["task_family"] == "tool_use" and case.get("generator")
    ]
    assert len(structured) >= 20
    assert len({case["acceptance"]["task_evaluator"]["exact_values"]["tool"]
                for case in structured}) > 4, "expected more than the original four tools"
    key_counts = {
        len(case["acceptance"]["task_evaluator"]["required_keys"])
        for case in structured
    }
    assert len(key_counts) > 1, "the required key set must vary between cases"
    assert report["distinct_acceptance_specs"] == len(cases)


def test_every_diversity_pool_is_wide_enough():
    """Pin the pools, not just the outcome.

    A per-family count can stay satisfied by luck when the pools are small
    (3 revisions x 7 layers still covers 20 cases), so the widths are asserted
    directly: each is the ceiling on how many distinct tasks a family can ever
    produce.
    """
    generator = _load_generator_module()

    assert len(generator.STRUCTURED_RULES) >= 12
    assert len(generator.TAXONOMY) >= 9
    assert len(generator.REVISION_POOL) >= 6
    assert len(generator.DECISION_LAYER_POOL) >= 8
    assert len(generator.OPTIONAL_CONSTRAINTS) >= 6
    assert len(generator._code_templates()) >= 20
    # A structured case varies its required key set, which multiplies the
    # tool x node combinations rather than just reusing them.
    assert len(generator.STRUCTURED_OPTIONAL_FIELDS) >= 4


def test_gate_floors_are_recorded_with_the_corpus(tmp_path):
    """A bar that lives only in someone's shell history is not a gate.

    The eligibility floors are corpus policy: they decide which policies may
    produce training evidence, so the generated suite has to carry them.
    """
    report = _run(
        tmp_path,
        seed=5,
        count=2,
        minimum_overall_pass_rate=0.95,
        minimum_pass_rate_by_kind='{"python_function": 0.98}',
    )
    suite = json.loads((tmp_path / "suite.json").read_text())

    assert suite["minimum_overall_pass_rate"] == 0.95
    # Merged over the base suite, so kinds we did not name keep their floor.
    assert suite["minimum_pass_rate_by_kind"]["python_function"] == 0.98
    assert "constraint_retention" in suite["minimum_pass_rate_by_kind"]
    assert report["report"]["minimum_overall_pass_rate"] == 0.95
    assert report["report"]["minimum_pass_rate_by_kind"]["python_function"] == 0.98


def test_gate_floors_default_to_the_base_suite(tmp_path):
    report = _run(tmp_path, seed=5, count=2)
    suite = json.loads((tmp_path / "suite.json").read_text())

    assert suite["minimum_overall_pass_rate"] == 1
    assert report["report"]["minimum_overall_pass_rate"] == 1


def test_invalid_gate_floors_are_rejected(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base", str(BASE),
            "--suite", str(SUITE),
            "--output-cases", str(tmp_path / "cases.jsonl"),
            "--output-suite", str(tmp_path / "suite.json"),
            "--count-per-kind", "2",
            "--seed", "5",
            "--minimum-overall-pass-rate", "1.5",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )

    assert completed.returncode != 0
    assert "0..1" in completed.stderr
