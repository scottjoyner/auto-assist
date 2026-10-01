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
# A repeated acceptance spec is the same task in different clothes: the exporter
# anchors duplicates into one split group so they cannot leak, which also means
# every duplicate is a corpus slot that teaches nothing. Draw again instead.
DISTINCT_ATTEMPTS = 24


def _spec_key(case: dict[str, Any]) -> str:
    return json.dumps(
        case.get("acceptance", {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

EVALUATION_SUITE = "assistx_task_quality.v1"
TAXONOMY = {
    "secret-query-param": "credential placed in URL query string",
    "shell-true": "subprocess executes with shell=True",
    "missing-timeout": "outbound requests call has no timeout",
    "tls-disabled": "TLS verification explicitly disabled",
    "logged-secret": "secret value written to a log record",
    "broad-except": "exception handler swallows every error",
    "mutable-default": "mutable default argument shared across calls",
    "hardcoded-credential": "credential hardcoded in source",
    "unchecked-index": "sequence index used without a bounds check",
}
TAXONOMY_ORDER = list(TAXONOMY)

# The context family had 3 revisions x 3 layers = 9 distinct acceptance specs,
# which made it the thinnest of the four. These pools widen every axis.
REVISION_POOL = [
    "abc123", "def456", "789abc", "0f1e2d", "a19b7c", "5e6d70",
]
DECISION_LAYER_POOL = [
    "my-jev-advisory",
    "assistx-shadow",
    "control-room-observer",
    "evidence-replay",
    "drill-sandbox",
    "telemetry-only",
    "operator-review",
    "plan-only",
]
OPTIONAL_CONSTRAINTS = [
    "EVIDENCE_MODE=READ-ONLY",
    "POLICY_BUNDLE_SHA=UNPINNED",
    "NODE_SCOPE=ADVISORY",
    "FAIL_CLOSED=TRUE",
    "ROUTING_ENABLED=FALSE",
    "SCOPE=LOCAL-ONLY",
]

CODE_PROMPT = (
    "Return only Python source defining exactly one function named "
    "{name}. {rule} Do not import modules, define helpers, print, "
    "or include Markdown. Use only assignments, for-loops, "
    "if-statements, comprehensions, and builtin functions. "
    "Attribute access and method calls are forbidden: no .append, "
    ".sort, .keys, or any other dot access — build lists with "
    "comprehensions or +. No while loops, try/except, or lambda."
)
STRUCTURED_RULES = {
    "health_check": "Represent a read-only health observation.",
    "list_models": "Represent a read-only listing of loaded models.",
    "probe_capacity": "Represent a read-only capacity probe.",
    "verify_backup": "Represent a read-only backup verification.",
    "describe_task": "Represent a read-only task description lookup.",
    "list_sessions": "Represent a read-only listing of sessions.",
    "read_event_log": "Represent a read-only event log read.",
    "fetch_queue_depth": "Represent a read-only queue depth probe.",
    "get_build_info": "Represent a read-only build metadata lookup.",
    "list_mounts": "Represent a read-only listing of mounted volumes.",
    "check_disk_usage": "Represent a read-only disk usage probe.",
    "list_endpoints": "Represent a read-only listing of service endpoints.",
}
# Optional fields a structured observation may also carry. The prompt states
# exactly which are required, so the task stays "read this spec and emit it"
# rather than "recall one fixed schema".
STRUCTURED_OPTIONAL_FIELDS = {
    "schema_version": lambda rng: f"v{rng.randint(1, 9)}",
    "detail_level": lambda rng: rng.choice(["summary", "full", "minimal"]),
    "source": lambda rng: rng.choice(["telemetry", "control-plane", "registry"]),
    "observed_at_unix_ms": lambda rng: int(
        rng.randint(1_700_000_000, 1_800_000_000)
    ),
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
            "param_order": ["values", "threshold"],
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
            "param_order": ["values"],
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
            "param_order": ["values", "low", "high"],
            "rule": "It must return the sum of all elements of values, "
                    "with that total clamped into the inclusive range "
                    "[low, high].",
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
            "param_order": ["values"],
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
            "param_order": ["values", "k"],
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
            "param_order": ["values"],
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
            "param_order": ["values"],
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
            "param_order": ["values", "low", "high"],
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
            "param_order": ["values"],
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
            "param_order": ["values"],
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
        {
            "name": "count_below",
            "param_order": ["values", "threshold"],
            "rule": "It must return how many elements of values are "
                    "strictly less than threshold.",
            "source": (
                "def count_below(values, threshold):\n"
                "    total = 0\n"
                "    for value in values:\n"
                "        if value < threshold:\n"
                "            total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
                "threshold": rng.randint(-10, 10),
            },
        },
        {
            "name": "second_largest",
            "param_order": ["values"],
            "rule": "It must return the second largest element of values, or 0 "
                    "when values holds fewer than two elements. Duplicates "
                    "count, so a repeated maximum is returned as-is.",
            "source": (
                "def second_largest(values):\n"
                "    if len(values) < 2:\n"
                "        return 0\n"
                "    ranked = sorted(values)\n"
                "    return ranked[len(ranked) - 2]\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 10))],
            },
        },
        {
            "name": "longest_equal_run",
            "param_order": ["values"],
            "rule": "It must return the length of the longest run of equal "
                    "consecutive elements in values, or 0 when values is empty.",
            "source": (
                "def longest_equal_run(values):\n"
                "    best = 0\n"
                "    current = 0\n"
                "    previous = 0\n"
                "    for index in range(len(values)):\n"
                "        value = values[index]\n"
                "        if index > 0 and value == previous:\n"
                "            current = current + 1\n"
                "        else:\n"
                "            current = 1\n"
                "        if current > best:\n"
                "            best = current\n"
                "        previous = value\n"
                "    return best\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-3, 3) for _ in range(rng.randint(0, 14))],
            },
        },
        {
            "name": "total_span",
            "param_order": ["values"],
            "rule": "It must return the difference between the largest and "
                    "smallest element of values, or 0 when values is empty.",
            "source": (
                "def total_span(values):\n"
                "    if len(values) == 0:\n"
                "        return 0\n"
                "    return max(values) - min(values)\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-30, 30) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "even_index_sum",
            "param_order": ["values"],
            "rule": "It must return the sum of the elements of values that sit "
                    "at an even index, where indexing starts at 0.",
            "source": (
                "def even_index_sum(values):\n"
                "    total = 0\n"
                "    for index in range(len(values)):\n"
                "        if index % 2 == 0:\n"
                "            total = total + values[index]\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "index_of_max",
            "param_order": ["values"],
            "rule": "It must return the index of the first occurrence of the "
                    "largest element of values, or -1 when values is empty.",
            "source": (
                "def index_of_max(values):\n"
                "    best = -1\n"
                "    best_index = -1\n"
                "    for index in range(len(values)):\n"
                "        if best_index == -1 or values[index] > best:\n"
                "            best = values[index]\n"
                "            best_index = index\n"
                "    return best_index\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "clamped_total",
            "param_order": ["values", "low", "high"],
            "rule": "It must return the sum of values after clamping each "
                    "element into the inclusive range low to high.",
            "source": (
                "def clamped_total(values, low, high):\n"
                "    total = 0\n"
                "    for value in values:\n"
                "        if value < low:\n"
                "            value = low\n"
                "        if value > high:\n"
                "            value = high\n"
                "        total = total + value\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-30, 30) for _ in range(rng.randint(0, 12))],
                "low": rng.randint(-5, 0),
                "high": rng.randint(0, 5),
            },
        },
        {
            "name": "distinct_count",
            "param_order": ["values"],
            "rule": "It must return how many distinct integers appear in "
                    "values.",
            "source": (
                "def distinct_count(values):\n"
                "    return len(set(values))\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-6, 6) for _ in range(rng.randint(0, 14))],
            },
        },
        {
            "name": "total_drift",
            "param_order": ["values"],
            "rule": "It must return the sum of the absolute differences "
                    "between consecutive elements of values, or 0 when values "
                    "holds fewer than two elements.",
            "source": (
                "def total_drift(values):\n"
                "    total = 0\n"
                "    for index in range(1, len(values)):\n"
                "        difference = values[index] - values[index - 1]\n"
                "        if difference < 0:\n"
                "            difference = -difference\n"
                "        total = total + difference\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "mean_of_evens",
            "param_order": ["values"],
            "rule": "It must return the arithmetic mean of the even elements "
                    "of values, or 0 when values holds no even element.",
            "source": (
                "def mean_of_evens(values):\n"
                "    total = 0\n"
                "    count = 0\n"
                "    for value in values:\n"
                "        if value % 2 == 0:\n"
                "            total = total + value\n"
                "            count = count + 1\n"
                "    if count == 0:\n"
                "        return 0\n"
                "    return total / count\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "first_above",
            "param_order": ["values", "threshold"],
            "rule": "It must return the first element of values that is "
                    "strictly greater than threshold, or 0 when no element "
                    "qualifies.",
            "source": (
                "def first_above(values, threshold):\n"
                "    for value in values:\n"
                "        if value > threshold:\n"
                "            return value\n"
                "    return 0\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
                "threshold": rng.randint(-10, 10),
            },
        },
        {
            "name": "local_maxima_count",
            "param_order": ["values"],
            "rule": "It must return how many interior elements of values are "
                    "strictly greater than both neighbours. Sequences shorter "
                    "than three elements have no interior elements.",
            "source": (
                "def local_maxima_count(values):\n"
                "    total = 0\n"
                "    for index in range(1, len(values) - 1):\n"
                "        if values[index] > values[index - 1] and "
                "values[index] > values[index + 1]:\n"
                "            total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-10, 10) for _ in range(rng.randint(0, 12))],
            },
        },
        {
            "name": "chunk_totals",
            "param_order": ["values", "k"],
            "rule": "It must return the list of totals of consecutive chunks of "
                    "k elements of values, in order. A trailing chunk shorter "
                    "than k is included as-is. It must return an empty list "
                    "when k is not positive.",
            "source": (
                "def chunk_totals(values, k):\n"
                "    if k <= 0:\n"
                "        return []\n"
                "    totals = []\n"
                "    for start in range(0, len(values), k):\n"
                "        total = 0\n"
                "        for index in range(start, start + k):\n"
                "            if index < len(values):\n"
                "                total = total + values[index]\n"
                "        totals = totals + [total]\n"
                "    return totals\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-10, 10) for _ in range(rng.randint(0, 12))],
                "k": rng.randint(1, 4),
            },
        },
        {
            "name": "digit_sum",
            "param_order": ["value"],
            "rule": "It must return the sum of the decimal digits of value, "
                    "ignoring any sign. It must return 0 for 0.",
            "source": (
                "def digit_sum(value):\n"
                "    if value < 0:\n"
                "        value = -value\n"
                "    total = 0\n"
                "    for digit in str(value):\n"
                "        total = total + int(digit)\n"
                "    return total\n"
            ),
            "params": lambda rng: {"value": rng.randint(0, 100000)},
        },
        {
            "name": "word_count",
            "param_order": ["words"],
            "rule": "It must return how many items words holds.",
            "source": (
                "def word_count(words):\n"
                "    return len(words)\n"
            ),
            "params": lambda rng: {
                "words": [
                    "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(1, 8)))
                    for _ in range(rng.randint(0, 6))
                ],
            },
        },
        {
            "name": "longest_word_length",
            "param_order": ["words"],
            "rule": "It must return the length of the longest item of words, or "
                    "0 when words is empty.",
            "source": (
                "def longest_word_length(words):\n"
                "    best = 0\n"
                "    for word in words:\n"
                "        if len(word) > best:\n"
                "            best = len(word)\n"
                "    return best\n"
            ),
            "params": lambda rng: {
                "words": [
                    "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(1, 12)))
                    for _ in range(rng.randint(0, 6))
                ],
            },
        },
        {
            "name": "count_shorter_words",
            "param_order": ["words", "k"],
            "rule": "It must return how many items of words are strictly "
                    "shorter than k characters.",
            "source": (
                "def count_shorter_words(words, k):\n"
                "    total = 0\n"
                "    for word in words:\n"
                "        if len(word) < k:\n"
                "            total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "words": [
                    "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(1, 10)))
                    for _ in range(rng.randint(0, 6))
                ],
                "k": rng.randint(1, 10),
            },
        },
        {
            "name": "is_palindrome_word",
            "param_order": ["word"],
            "rule": "It must return 1 when word reads exactly the same "
                    "forwards and backwards, and 0 otherwise. An empty word is "
                    "a palindrome.",
            "source": (
                "def is_palindrome_word(word):\n"
                "    length = len(word)\n"
                "    for index in range(length // 2):\n"
                "        if word[index] != word[length - 1 - index]:\n"
                "            return 0\n"
                "    return 1\n"
            ),
            "params": lambda rng: {
                "word": "".join(rng.choice("abcba") for _ in range(rng.randint(1, 6))),
            },
        },
        {
            "name": "count_above_average",
            "param_order": ["values"],
            "rule": "It must return how many elements of values are strictly "
                    "greater than the arithmetic mean of values, or 0 when "
                    "values is empty.",
            "source": (
                "def count_above_average(values):\n"
                "    if len(values) == 0:\n"
                "        return 0\n"
                "    average = sum(values) / len(values)\n"
                "    total = 0\n"
                "    for value in values:\n"
                "        if value > average:\n"
                "            total = total + 1\n"
                "    return total\n"
            ),
            "params": lambda rng: {
                "values": [rng.randint(-20, 20) for _ in range(rng.randint(0, 12))],
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
    order = list(template["param_order"])
    params = template["params"](rng)
    # Arguments follow the template's declared order, so a new task shape needs
    # no change here. The reference run below proves the shape is usable.
    if set(params) != set(order):
        raise AssertionError(
            f"{template['name']} produced {sorted(params)}, "
            f"expected {sorted(order)}"
        )
    arg_sets = [{"args": [params[key] for key in order]}]
    # Add a couple of extra random vectors of the same shape for coverage.
    extra = []
    for _ in range(2):
        clone = template["params"](rng)
        if set(clone) != set(order):
            continue
        extra.append({"args": [clone[key] for key in order]})
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


def _template_index() -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in _code_templates()}


def _params_from_args(name: str, args: list[Any]) -> dict[str, Any]:
    order = _template_index()[name]["param_order"]
    if len(args) != len(order):
        raise AssertionError(
            f"{name} got {len(args)} arguments, expected {len(order)}"
        )
    return dict(zip(order, args, strict=True))


def _generate_structured_case(
    rng: random.Random,
    ordinal: int,
    used: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tool = rng.choice(sorted(STRUCTURED_RULES))
    node = rng.choice(NODES)
    status = rng.choice(["ok", "degraded", "stale", "partial"])
    exact: dict[str, Any] = {
        "tool": tool,
        "node": node,
        "status": status,
        "read_only": True,
        "mutation_allowed": False,
    }
    # One or two optional fields, so the required key set varies per case.
    optional = sorted(rng.sample(
        sorted(STRUCTURED_OPTIONAL_FIELDS),
        rng.randint(1, len(STRUCTURED_OPTIONAL_FIELDS)),
    ))
    for field_name in optional:
        exact[field_name] = STRUCTURED_OPTIONAL_FIELDS[field_name](rng)
    keys = sorted(exact)
    base_id = f"taskq-tool-{tool.replace('_', '-')}-{node}"
    case_id = base_id if base_id not in used else f"{base_id}-{ordinal:02d}"
    used.add(case_id)
    rendered = ", ".join(
        f"{key}={json.dumps(exact[key])}" for key in keys
    )
    prompt = (
        "Return exactly one JSON object and nothing else representing a "
        f"read-only {tool.replace('_', ' ')} observation for node {node}. "
        f"Required values: {rendered}."
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
        "logged-secret": (
            '    log.info(f"calling with token {token}")'
        ),
        "broad-except": (
            "    try:\n"
            "        return requests.get(f\"{base_url}/x\", timeout=5)\n"
            "    except Exception:\n"
            "        return None"
        ),
        "mutable-default": (
            "    cache = {}\n"
            "    def remember(key, store=cache):\n"
            "        store[key] = 1"
        ),
        "hardcoded-credential": (
            '    admin = {"user": "root", "password": "hunter2"}'
        ),
        "unchecked-index": (
            "    def pick(items, position):\n"
            "        return items[position]"
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
    revision = f"llama.cpp@{rng.choice(REVISION_POOL)}"
    handle = f"opaque:qwen38-q4-{rng.choice(['a', 'b', 'c', 'd', 'e', 'f'])}"
    layer = rng.choice(DECISION_LAYER_POOL)
    # Which optional constraint lines must be echoed back varies per case, so
    # the required set is not a fixed four lines.
    optional = rng.sample(
        OPTIONAL_CONSTRAINTS,
        rng.randint(1, len(OPTIONAL_CONSTRAINTS)),
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
    ] + optional
    repeats = rng.randint(6, 30)
    system = "\n".join(facts) + "\n\n" + DISTRACTORS * repeats
    wanted = [
        f"DECISION_LAYER={layer}",
        f"RUNTIME_REVISION={revision}",
        "AUTHORITY_MODE=ADVISORY-ONLY",
        "mutation_allowed=false",
    ] + optional
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

    seen_specs = {_spec_key(row) for row in base_rows}
    duplicates_skipped = 0

    def emit(build) -> None:
        nonlocal duplicates_skipped
        for attempt in range(DISTINCT_ATTEMPTS):
            case, meta = build()
            key = _spec_key(case)
            if key in seen_specs:
                continue
            seen_specs.add(key)
            case["case_id"] = f"{case['case_id']}-{attempt:02d}" if attempt else case["case_id"]
            generated.append(case)
            answers[case["case_id"]] = meta["answer"]
            return
        duplicates_skipped += 1

    templates = _code_templates()
    for ordinal in range(1, args.count_per_kind + 1):
        template = templates[(ordinal - 1) % len(templates)]
        emit(lambda t=template, o=ordinal: _generate_code_case(rng, t, o))
    for ordinal in range(1, args.count_per_kind + 1):
        emit(lambda o=ordinal: _generate_structured_case(rng, o, used_ids))
    for ordinal in range(1, args.count_per_kind + 1):
        emit(lambda o=ordinal: _generate_review_case(rng, o))
    for ordinal in range(1, args.count_per_kind + 1):
        emit(lambda o=ordinal: _generate_context_case(rng, o))

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
    description = str(suite.get("description") or "")
    base_description = description.split(
        " Expanded deterministically by "
    )[0].rstrip(".")
    suite["description"] = (
        base_description
        + ". Expanded deterministically by "
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

    generated_specs = {_spec_key(case) for case in generated}
    code_generated = [
        case
        for case in generated
        if case["task_family"] == "coding"
        and isinstance(
            case.get("acceptance", {}).get("task_evaluator"),
            dict,
        )
        and "function_name" in case["acceptance"]["task_evaluator"]
    ]
    report = {
        "seed": args.seed,
        "count_per_kind": args.count_per_kind,
        "generated": len(generated),
        "replaced": len(replaced_rows),
        # Diversity is the ceiling on what a larger corpus can teach, so the
        # report states it instead of leaving it to be inferred from row count.
        "distinct_acceptance_specs": len(
            {_spec_key(row) for row in merged}
        ),
        "generated_distinct_acceptance_specs": len(generated_specs),
        "code_templates_available": len(templates),
        "code_templates_used": len(
            {
                case["acceptance"]["task_evaluator"]["function_name"]
                for case in code_generated
            }
        ),
        "distinct_specs_skipped": duplicates_skipped,
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
