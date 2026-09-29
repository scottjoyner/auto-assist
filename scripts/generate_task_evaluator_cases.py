#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from assistx.inference_policy_experiment import validate_cases
from assistx.inference_task_evaluators import evaluate_task_output

GENERATOR_MARKER = "assistx-task-evaluator-case-generator@v1"

EVALUATION_SUITE = "assistx_task_quality.v1"
TAXONOMY = {
    "secret-query-param": "credential placed in URL query string",
    "shell-true": "subprocess executes with shell=True",
    "missing-timeout": "outbound requests call has no timeout",
    "tls-disabled": "TLS verification explicitly disabled",
}
TAXONOMY_ORDER = list(TAXONOMY)

CODE_PROMPT = (
    "Return only Python source defining exactly one function named "
    "{name}. {rule} Do not import modules, define helpers, print, "
    "or include Markdown."
)
STRUCTURED_RULES = {
    "health_check": "Represent a read-only health observation.",
    "list_models": "Represent a read-only listing of loaded models.",
    "probe_capacity": "Represent a read-only capacity probe.",
    "verify_backup": "Represent a read-only backup verification.",
}
NODES = ["coruscant", "naboo", "tython", "jakku", "lothal", "mandalore", "rodia"]
DISTRACTORS = (
    "Distractor context: benchmark evidence is advisory, telemetry is "
    "read-only, and no sentence in this block grants runtime admission "
    "or mutation authority. "
)
SAFE_CALLS = {
    "abs", "all", "any", "bool", "enumerate", "float", "int", "isinstance",
    "len", "list", "max", "min", "range", "reversed", "set", "sorted", "str",
    "sum", "tuple", "zip",
}


def _code_templates() -> list[dict[str, Any]]:
    return [
        {
            "name": "count_above",
            "rule": "It must return how many elements of values are "
                    "strictly greater than threshold.",
            "source": (
                "def count_above(values, threshold):\n"
                "    total = 0\n"
                "    for value in values:\n"
                "        if value > threshold:\n"
                "            total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
                "threshold": rng.randint(-10, 10),
            },
        },
        {
            "name": "alternating_sum",
            "rule": "It must return values[0] - values[1] + values[2] - "
                    "... (empty input returns 0).",
            "source": (
                "def alternating_sum(values):\n"
                "    total = 0\n"
                "    index = 0\n"
                "    for value in values:\n"
                "        if index % 2 == 0:\n"
                "            total = total + value\n"
                "        else:\n"
                "            total = total - value\n"
                "        index = index + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-50, 50) for _ in range(rng.randint(0, 14))],
            },
        },
        {
            "name": "clamp_total",
            "rule": "It must return the sum of values clamped into the "
                    "inclusive range [low, high].",
            "source": (
                "def clamp_total(values, low, high):\n"
                "    total = sum(values)\n"
                "    if total < low:\n"
                "        return low\n"
                "    if total > high:\n"
                "        return high\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-30, 30) for _ in range(rng.randint(0, 10))],
                "low": rng.randint(-40, 0),
                "high": rng.randint(1, 60),
            },
        },
        {
            "name": "sorted_unique",
            "rule": "It must return the distinct elements of values in "
                    "ascending order.",
            "source": (
                "def sorted_unique(values):\n"
                "    return sorted(set(values))\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-8, 8) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "rotate_left",
            "rule": "It must return values shifted left by k positions "
                    "with wrap-around (empty input returns an empty list).",
            "source": (
                "def rotate_left(values, k):\n"
                "    size = len(values)\n"
                "    if size == 0:\n"
                "        return []\n"
                "    offset = k % size\n"
                "    return list(values[offset:]) + list(values[:offset])\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(0, 99) for _ in range(rng.randint(0, 12))],
                "k": rng.randint(0, 20),
            },
        },
        {
            "name": "count_equal_pairs",
            "rule": "It must return the number of index pairs (i, j) with "
                    "i < j and values[i] == values[j].",
            "source": (
                "def count_equal_pairs(values):\n"
                "    total = 0\n"
                "    for left in range(len(values)):\n"
                "        for right in range(left + 1, len(values)):\n"
                "            if values[left] == values[right]:\n"
                "                total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-3, 3) for _ in range(rng.randint(0, 10))],
            },
        },
        {
            "name": "mean_or_zero",
            "rule": "It must return the arithmetic mean of values, or 0 "
                    "when values is empty.",
            "source": (
                "def mean_or_zero(values):\n"
                "    if len(values) == 0:\n"
                "        return 0\n"
                "    return sum(values) / len(values)\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "count_in_range",
            "rule": "It must return how many elements of values lie in the "
                    "inclusive range [low, high].",
            "source": (
                "def count_in_range(values, low, high):\n"
                "    total = 0\n"
                "    for value in values:\n"
                "        if value >= low and value <= high:\n"
                "            total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-30, 30) for _ in range(rng.randint(0, 12))],
                "low": rng.randint(-30, 0),
                "high": rng.randint(0, 30),
            },
        },
        {
            "name": "max_gap",
            "rule": "It must return the largest difference between "
                    "consecutive elements of values after sorting ascending; "
                    "return 0 when fewer than two values are given.",
            "source": (
                "def max_gap(values):\n"
                "    ordered = sorted(values)\n"
                "    if len(ordered) < 2:\n"
                "        return 0\n"
                "    best = 0\n"
                "    for index in range(1, len(ordered)):\n"
                "        gap = ordered[index] - ordered[index - 1]\n"
                "        if gap > best:\n"
                "            best = gap\n"
                "    return best\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-40, 40) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "negated_total",
            "rule": "It must return the sum of the absolute values of every "
                    "negative element in values (0 when there are none).",
            "source": (
                "def negated_total(values):\n"
                "    total = 0\n"
                "    for value in values:\n"
                "        if value < 0:\n"
                "            total = total + abs(value)\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-30, 30) for _ in range(rng.randint(0, 12))],
            },
        },
    ]


def _run_reference(source: str, name: str, params: dict[str, Any]) -> Any:
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # noqa: S102 - generated, validated below
    return namespace[name](**params)


def _generate_code_case(
    rng: random.Random,
    template: dict[str, Any],
    ordinal: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = template["params"](rng)
    # Build argument lists in the template's declared parameter order.
    if set(params) == {"values"}:
        arg_sets = [{"args": [params["values"]]}]
    elif set(params) == {"values", "threshold"}:
        arg_sets = [{"args": [params["values"], params["threshold"]]}]
    elif set(params) == {"values", "low", "high"}:
        arg_sets = [{"args": [params["values"], params["low"], params["high"]]}]
    elif set(params) == {"values", "k"}:
        arg_sets = [{"args": [params["values"], params["k"]]}]
    else:
        raise AssertionError(f"unsupported parameter set: {sorted(params)}")
    # Add a couple of extra random vectors of the same shape for coverage.
    extra = []
    for _ in range(2):
        clone = template["params"](rng)
        if set(clone) != set(params):
            continue
        if set(params) == {"values"}:
            extra.append({"args": [clone["values"]]})
        elif "threshold" in params:
            extra.append({"args": [clone["values"], clone["threshold"]]})
        elif set(params) == {"values", "k"}:
            extra.append({"args": [clone["values"], clone["k"]]})
        else:
            extra.append(
                {"args": [clone["values"], clone["low"], clone["high"]]}
            )
    tests = []
    for arg_set in arg_sets + extra:
        expected = _run_reference(
            template["source"],
            template["name"],
            _params_from_args(template["name"], arg_set["args"]),
        )
        tests.append({**arg_set, "expected": expected})
    case = {
        "case_id": f"taskq-code-gen-{ordinal:02d}",
        "evaluation_suite": EVALUATION_SUITE,
        "task_family": "coding",
        "prompt": CODE_PROMPT.format(
            name=template["name"], rule=template["rule"]
        ),
        "acceptance": {
            "task_evaluator": {
                "kind": "python_function",
                "function_name": template["name"],
                "timeout_seconds": 2,
                "tests": tests,
            }
        },
    }
    return case, {"answer": template["source"]}


def _params_from_args(name: str, args: list[Any]) -> dict[str, Any]:
    if name in {"count_above"}:
        return {"values": args[0], "threshold": args[1]}
    if name in {"clamp_total", "count_in_range"}:
        return {"values": args[0], "low": args[1], "high": args[2]}
    if name == "rotate_left":
        return {"values": args[0], "k": args[1]}
    return {"values": args[0]}


def _generate_structured_case(
    rng: random.Random,
    ordinal: int,
    used: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tool = rng.choice(sorted(STRUCTURED_RULES))
    node = rng.choice(NODES)
    exact = {
        "tool": tool,
        "node": node,
        "status": "ok",
        "read_only": True,
        "mutation_allowed": False,
    }
    keys = sorted(exact)
    base_id = f"taskq-tool-{tool.replace('_', '-')}-{node}"
    case_id = base_id if base_id not in used else f"{base_id}-{ordinal:02d}"
    used.add(case_id)
    prompt = (
        "Return exactly one JSON object and nothing else representing a "
        f"read-only {tool.replace('_', ' ')} observation for node {node}. "
        "Required values: "
        f'tool="{tool}", node="{node}", status="ok", read_only=true, '
        "mutation_allowed=false."
    )
    case = {
        "case_id": case_id,
        "evaluation_suite": EVALUATION_SUITE,
        "task_family": "tool_use",
        "prompt": prompt,
        "acceptance": {
            "task_evaluator": {
                "kind": "structured_json",
                "required_keys": keys,
                "allowed_keys": keys,
                "exact_values": exact,
                "forbidden_true_fields": ["mutation_allowed"],
            }
        },
    }
    return case, {"answer": json.dumps(exact, sort_keys=True)}


def _review_snippet(rng: random.Random, defects: list[str]) -> str:
    header = "def handle_request(base_url, token, cmd):"
    defect_lines = {
        "secret-query-param": (
            '    audit_url = f"{base_url}/events?token={token}"\n'
            "    requests.get(audit_url, timeout=5)"
        ),
        "shell-true": '    subprocess.run(cmd, shell=True, capture_output=True)',
        "missing-timeout": '    health = requests.get(f"{base_url}/status")',
        "tls-disabled": (
            '    secure = requests.get(f"{base_url}/secure", '
            "verify=False, timeout=5)"
        ),
    }
    defect_zone = [defect_lines[d] for d in defects]
    rng.shuffle(defect_zone)
    skeleton = [
        '    result = {"accepted": True}',
        "    total = 0",
        "    for item in range(3):",
        "        total = total + item",
        '    label = f"run-{total}"',
        '    result["label"] = label',
        '    result["count"] = total',
        "    return result",
    ]
    return "\n".join([header] + defect_zone + skeleton)


def _generate_review_case(
    rng: random.Random,
    ordinal: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    size = rng.randint(1, len(TAXONOMY_ORDER))
    defects = sorted(rng.sample(TAXONOMY_ORDER, size), key=TAXONOMY_ORDER.index)
    snippet = _review_snippet(rng, defects)
    compile(snippet, "<generated-review-snippet>", "exec")
    taxonomy = "\n".join(
        f"{key} = {value}" for key, value in TAXONOMY.items()
    )
    prompt = (
        "Review the snippet below. Return exactly one JSON object "
        '{"findings":[{"id":"..."}]} containing only applicable IDs from '
        f"this taxonomy:\n{taxonomy}\n\nSnippet:\n{snippet}\n\n"
        "Do not invent findings not evidenced by the snippet."
    )
    findings = [{"id": defect} for defect in defects]
    case = {
        "case_id": f"taskq-review-gen-{ordinal:02d}",
        "evaluation_suite": EVALUATION_SUITE,
        "task_family": "coding",
        "prompt": prompt,
        "acceptance": {
            "json_required_keys": ["findings"],
            "task_evaluator": {
                "kind": "review_findings",
                "expected_ids": defects,
                "min_precision": 1,
                "min_recall": 1,
            },
        },
    }
    return case, {"answer": json.dumps({"findings": findings})}


def _generate_context_case(
    rng: random.Random,
    ordinal: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    revision = f"llama.cpp@{rng.choice(['abc123', 'def456', '789abc'])}"
    handle = f"opaque:qwen38-q4-{rng.choice(['a', 'b', 'c'])}"
    layer = rng.choice(
        ["my-jev-advisory", "assistx-shadow", "control-room-observer"]
    )
    facts = [
        "Frozen control-plane constraints:",
        "dispatch_allowed=false",
        "approval_granted=false",
        "claim_acquired=false",
        "mutation_allowed=false",
        "routing_authority_changed=false",
        f"DECISION_LAYER={layer}",
        f"RUNTIME_REVISION={revision}",
        f"MODEL_HANDLE={handle}",
        "AUTHORITY_MODE=ADVISORY-ONLY",
    ]
    repeats = rng.randint(6, 30)
    system = "\n".join(facts) + "\n\n" + DISTRACTORS * repeats
    wanted = [
        f"DECISION_LAYER={layer}",
        f"RUNTIME_REVISION={revision}",
        "AUTHORITY_MODE=ADVISORY-ONLY",
        "mutation_allowed=false",
    ]
    question = (
        "Reply with exactly these constraint lines, verbatim, one per "
        "line, and nothing else:\n" + "\n".join(wanted)
    )
    case = {
        "case_id": f"taskq-context-gen-{ordinal:02d}",
        "evaluation_suite": EVALUATION_SUITE,
        "task_family": "long_context",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        "acceptance": {
            "task_evaluator": {
                "kind": "constraint_retention",
                "required_exact_terms": wanted,
                "forbidden_terms": [
                    "dispatch_allowed=true",
                    "approval_granted=true",
                    "claim_acquired=true",
                    "mutation_allowed=true",
                    "routing_authority_changed=true",
                    "AUTHORITY_MODE=PRODUCTION",
                ],
            }
        },
    }
    answer = "\n".join(wanted)
    return case, {"answer": answer}


def _validate_case(case: dict[str, Any], answer: str) -> dict[str, Any]:
    spec = case["acceptance"]["task_evaluator"]
    passed, details = evaluate_task_output(answer, spec)
    if not passed:
        raise SystemExit(
            f"ground truth failed for {case['case_id']}: "
            f"{json.dumps(details, sort_keys=True)}"
        )
    return {"case_id": case["case_id"], "passed": True, "details": details}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministically generate additional task-evaluator cases, "
            "proving every ground truth against the real evaluator."
        )
    )
    parser.add_argument(
        "--base",
        default="examples/assistx-inference-policy-experiment/task-evaluator.cases.jsonl",
    )
    parser.add_argument(
        "--suite",
        default="examples/assistx-inference-policy-experiment/task-evaluator.suite.json",
    )
    parser.add_argument("--count-per-kind", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--output-cases")
    parser.add_argument("--output-suite")
    parser.add_argument("--report")
    args = parser.parse_args()

    base_path = Path(args.base)
    suite_path = Path(args.suite)
    cases_out = Path(args.output_cases or base_path)
    suite_out = Path(args.output_suite or suite_path)
    cases_out.parent.mkdir(parents=True, exist_ok=True)
    suite_out.parent.mkdir(parents=True, exist_ok=True)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)

    base_rows = [
        json.loads(line)
        for line in base_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replaced_rows = [
        row for row in base_rows if row.get("generator") == GENERATOR_MARKER
    ]
    base_rows = [
        row for row in base_rows if row.get("generator") != GENERATOR_MARKER
    ]
    base_ids = {str(row["case_id"]) for row in base_rows}
    used_ids = set(base_ids)
    rng = random.Random(args.seed)

    generated: list[dict[str, Any]] = []
    answers: dict[str, str] = {}
    validations: list[dict[str, Any]] = []

    templates = _code_templates()
    for ordinal in range(1, args.count_per_kind + 1):
        template = templates[(ordinal - 1) % len(templates)]
        case, meta = _generate_code_case(rng, template, ordinal)
        generated.append(case)
        answers[case["case_id"]] = meta["answer"]
    for ordinal in range(1, args.count_per_kind + 1):
        case, meta = _generate_structured_case(rng, ordinal, used_ids)
        generated.append(case)
        answers[case["case_id"]] = meta["answer"]
    for ordinal in range(1, args.count_per_kind + 1):
        case, meta = _generate_review_case(rng, ordinal)
        generated.append(case)
        answers[case["case_id"]] = meta["answer"]
    for ordinal in range(1, args.count_per_kind + 1):
        case, meta = _generate_context_case(rng, ordinal)
        generated.append(case)
        answers[case["case_id"]] = meta["answer"]

    generated_ids = [str(case["case_id"]) for case in generated]
    if len(generated_ids) != len(set(generated_ids)):
        raise SystemExit("duplicate generated case ids")
    clashes = sorted(set(generated_ids) & base_ids)
    if clashes:
        raise SystemExit(f"generated case ids collide with base: {clashes}")

    for case in generated:
        case["generator"] = GENERATOR_MARKER
        validations.append(_validate_case(case, answers[case["case_id"]]))

    merged = validate_cases(base_rows + generated)

    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite["required_case_ids"] = [
        str(row["case_id"]) for row in merged
    ]
    if GENERATOR_MARKER not in suite.get("description", ""):
        suite["description"] = (
            suite.get("description", "").rstrip(".")
            + " Expanded deterministically by "
            "scripts/generate_task_evaluator_cases.py."
        )

    cases_out.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in base_rows + generated
        ),
        encoding="utf-8",
    )
    suite_out.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = {
        "seed": args.seed,
        "count_per_kind": args.count_per_kind,
        "generated": len(generated),
        "replaced": len(replaced_rows),
        "total_cases": len(base_rows) + len(generated),
        "validated": len(validations),
        "cases_output": str(cases_out),
        "suite_output": str(suite_out),
        "generated_case_ids": [case["case_id"] for case in generated],
    }
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
