from __future__ import annotations

import ast
import json
import subprocess
import sys
from typing import Any

SUPPORTED_EVALUATORS = {
    "structured_json",
    "review_findings",
    "constraint_retention",
    "python_function",
}

_SAFE_AST_NODES = {
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.Assign, ast.AnnAssign, ast.Expr, ast.If, ast.For, ast.Break,
    ast.Continue, ast.Pass, ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Subscript, ast.Slice,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
}

_SAFE_CALLS = {
    "abs", "all", "any", "bool", "enumerate", "float", "int", "len",
    "list", "max", "min", "range", "reversed", "set", "sorted", "str",
    "sum", "tuple", "zip",
}


def validate_task_evaluator_spec(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("task_evaluator must be an object")
    kind = str(raw.get("kind") or "").strip()
    if kind not in SUPPORTED_EVALUATORS:
        raise ValueError(
            "task_evaluator.kind must be one of: "
            + ", ".join(sorted(SUPPORTED_EVALUATORS))
        )
    spec = {**raw, "kind": kind}
    if kind == "structured_json":
        _require_list(spec, "required_keys")
        exact = spec.get("exact_values", {})
        if not isinstance(exact, dict):
            raise ValueError("structured_json.exact_values must be an object")
        allowed = spec.get("allowed_keys")
        if allowed is not None and not isinstance(allowed, list):
            raise ValueError("structured_json.allowed_keys must be a list")
    elif kind == "review_findings":
        expected = _require_list(spec, "expected_ids")
        if not expected:
            raise ValueError("review_findings.expected_ids cannot be empty")
        if len(expected) != len(set(map(str, expected))):
            raise ValueError("review_findings.expected_ids must be unique")
        min_recall = float(spec.get("min_recall", 1.0))
        min_precision = float(spec.get("min_precision", 1.0))
        if not 0 <= min_recall <= 1 or not 0 <= min_precision <= 1:
            raise ValueError("review precision/recall thresholds must be 0..1")
        spec["min_recall"] = min_recall
        spec["min_precision"] = min_precision
    elif kind == "constraint_retention":
        exact_terms = _require_list(spec, "required_exact_terms")
        if not exact_terms:
            raise ValueError(
                "constraint_retention.required_exact_terms cannot be empty"
            )
        forbidden = spec.get("forbidden_terms", [])
        if not isinstance(forbidden, list):
            raise ValueError(
                "constraint_retention.forbidden_terms must be a list"
            )
    elif kind == "python_function":
        function_name = str(spec.get("function_name") or "").strip()
        if not function_name.isidentifier():
            raise ValueError(
                "python_function.function_name must be a valid identifier"
            )
        tests = spec.get("tests")
        if not isinstance(tests, list) or not tests:
            raise ValueError("python_function.tests must be a non-empty list")
        normalized_tests: list[dict[str, Any]] = []
        for index, test in enumerate(tests):
            if not isinstance(test, dict):
                raise ValueError(
                    f"python_function.tests[{index}] must be an object"
                )
            args = test.get("args", [])
            kwargs = test.get("kwargs", {})
            if not isinstance(args, list):
                raise ValueError(
                    f"python_function.tests[{index}].args must be a list"
                )
            if not isinstance(kwargs, dict):
                raise ValueError(
                    f"python_function.tests[{index}].kwargs must be an object"
                )
            if "expected" not in test:
                raise ValueError(
                    f"python_function.tests[{index}].expected is required"
                )
            normalized_tests.append(
                {"args": args, "kwargs": kwargs, "expected": test["expected"]}
            )
        spec["function_name"] = function_name
        spec["tests"] = normalized_tests
        timeout = float(spec.get("timeout_seconds", 2.0))
        if not 0.1 <= timeout <= 5.0:
            raise ValueError(
                "python_function.timeout_seconds must be between 0.1 and 5"
            )
        spec["timeout_seconds"] = timeout
    return spec


def evaluate_task_output(
    output_text: str,
    raw_spec: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    spec = validate_task_evaluator_spec(raw_spec)
    kind = spec["kind"]
    if kind == "structured_json":
        return _evaluate_structured_json(output_text, spec)
    if kind == "review_findings":
        return _evaluate_review_findings(output_text, spec)
    if kind == "constraint_retention":
        return _evaluate_constraint_retention(output_text, spec)
    if kind == "python_function":
        return _evaluate_python_function(output_text, spec)
    raise AssertionError(f"unsupported evaluator: {kind}")


def _evaluate_structured_json(
    output_text: str,
    spec: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    parsed, error = _parse_json_object(output_text)
    if error:
        return False, {"kind": "structured_json", "passed": False, "parse_error": error}
    required = [str(key) for key in spec.get("required_keys", [])]
    missing = [key for key in required if key not in parsed]
    exact_values = spec.get("exact_values", {})
    exact_mismatches = {
        key: {"expected": expected, "observed": parsed.get(key)}
        for key, expected in exact_values.items()
        if parsed.get(key) != expected
    }
    allowed = spec.get("allowed_keys")
    unexpected: list[str] = []
    if isinstance(allowed, list):
        allowed_set = {str(key) for key in allowed}
        unexpected = sorted(
            str(key) for key in parsed if str(key) not in allowed_set
        )
    forbidden_true = [
        str(key) for key in spec.get("forbidden_true_fields", [])
    ]
    forbidden_true_observed = [
        key for key in forbidden_true if parsed.get(key) is True
    ]
    passed = not (
        missing or exact_mismatches or unexpected or forbidden_true_observed
    )
    return passed, {
        "kind": "structured_json", "passed": passed,
        "missing_keys": missing, "exact_value_mismatches": exact_mismatches,
        "unexpected_keys": unexpected,
        "forbidden_true_fields": forbidden_true_observed,
    }


def _evaluate_review_findings(
    output_text: str,
    spec: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    parsed, error = _parse_json_object(output_text)
    if error:
        return False, {"kind": "review_findings", "passed": False, "parse_error": error}
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return False, {
            "kind": "review_findings", "passed": False,
            "parse_error": "findings must be a list",
        }
    observed: list[str] = []
    malformed = 0
    for item in findings:
        if isinstance(item, str):
            finding_id = item
        elif isinstance(item, dict):
            finding_id = str(item.get("id") or "")
        else:
            finding_id = ""
        finding_id = finding_id.strip()
        if finding_id:
            observed.append(finding_id)
        else:
            malformed += 1
    observed_set = set(observed)
    expected = {str(value) for value in spec["expected_ids"]}
    true_positive = observed_set & expected
    false_positive = observed_set - expected
    false_negative = expected - observed_set
    precision = len(true_positive) / len(observed_set) if observed_set else 0.0
    recall = len(true_positive) / len(expected)
    passed = (
        malformed == 0
        and precision >= float(spec["min_precision"])
        and recall >= float(spec["min_recall"])
    )
    return passed, {
        "kind": "review_findings", "passed": passed,
        "expected_ids": sorted(expected), "observed_ids": sorted(observed_set),
        "true_positive_ids": sorted(true_positive),
        "false_positive_ids": sorted(false_positive),
        "false_negative_ids": sorted(false_negative),
        "precision": round(precision, 9), "recall": round(recall, 9),
        "malformed_findings": malformed,
        "required_min_precision": spec["min_precision"],
        "required_min_recall": spec["min_recall"],
    }


def _evaluate_constraint_retention(
    output_text: str,
    spec: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    required = [str(value) for value in spec["required_exact_terms"]]
    forbidden = [str(value) for value in spec.get("forbidden_terms", [])]
    missing = [value for value in required if value not in output_text]
    observed_forbidden = [value for value in forbidden if value in output_text]
    passed = not missing and not observed_forbidden
    return passed, {
        "kind": "constraint_retention", "passed": passed,
        "missing_required_exact_terms": missing,
        "observed_forbidden_terms": observed_forbidden,
    }


def _evaluate_python_function(
    output_text: str,
    spec: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    code = _extract_python_code(output_text)
    try:
        tree = ast.parse(code, mode="exec")
        _validate_python_ast(tree, spec["function_name"])
    except (SyntaxError, ValueError) as exc:
        return False, {
            "kind": "python_function", "passed": False,
            "validation_error": str(exc),
        }
    payload = {
        "code": code, "function_name": spec["function_name"],
        "tests": spec["tests"], "safe_calls": sorted(_SAFE_CALLS),
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _python_runner_source()],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=float(spec["timeout_seconds"]), check=False, env={},
        )
    except subprocess.TimeoutExpired:
        return False, {
            "kind": "python_function", "passed": False,
            "execution_error": "timeout",
        }
    if completed.returncode != 0:
        return False, {
            "kind": "python_function", "passed": False,
            "execution_error": completed.stderr.strip()[:500]
            or f"runner_exit_{completed.returncode}",
        }
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, {
            "kind": "python_function", "passed": False,
            "execution_error": "runner returned invalid JSON",
        }
    if not isinstance(result, dict):
        return False, {
            "kind": "python_function", "passed": False,
            "execution_error": "runner returned non-object",
        }
    passed = result.get("passed") is True
    return passed, {
        "kind": "python_function", "passed": passed,
        "function_name": spec["function_name"],
        "tests": result.get("tests", []),
        "passed_tests": result.get("passed_tests", 0),
        "total_tests": result.get("total_tests", len(spec["tests"])),
    }


def _validate_python_ast(tree: ast.AST, function_name: str) -> None:
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != function_name:
        raise ValueError(
            f"output must define exactly one function named {function_name}"
        )
    if len(tree.body) != 1:
        raise ValueError("output may contain only the target function")
    for node in ast.walk(tree):
        if type(node) not in _SAFE_AST_NODES:
            raise ValueError(
                f"unsafe or unsupported Python AST node: {type(node).__name__}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("only direct calls to safe builtins are allowed")
            if node.func.id not in _SAFE_CALLS:
                raise ValueError(f"call to {node.func.id!r} is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("dunder names are not allowed")


def _extract_python_code(output_text: str) -> str:
    stripped = output_text.strip()
    parsed, error = _parse_json_object(output_text)
    if error is None and isinstance(parsed.get("code"), str):
        return parsed["code"].strip()
    return stripped


def _parse_json_object(
    output_text: str,
) -> tuple[dict[str, Any], str | None]:
    text = output_text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc.msg}"
    if not isinstance(parsed, dict):
        return {}, "expected a JSON object"
    return parsed, None


def _require_list(spec: dict[str, Any], field: str) -> list[Any]:
    value = spec.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{spec.get('kind')}.{field} must be a list")
    return value


def _python_runner_source() -> str:
    return r'''
import json
import resource
import sys

# Defense in depth for model-generated code. The parent process already
# validates the AST; the child additionally limits CPU and address space.
try:
    resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
    resource.setrlimit(
        resource.RLIMIT_AS,
        (256 * 1024 * 1024, 256 * 1024 * 1024),
    )
except Exception:
    pass

payload = json.loads(sys.stdin.read())
safe_builtin_names = payload["safe_calls"]
source_builtins = __builtins__
if isinstance(source_builtins, dict):
    builtins_map = source_builtins
else:
    builtins_map = source_builtins.__dict__
safe_builtins = {
    name: builtins_map[name]
    for name in safe_builtin_names
    if name in builtins_map
}
namespace = {"__builtins__": safe_builtins}
exec(compile(payload["code"], "<candidate>", "exec"), namespace, namespace)
fn = namespace[payload["function_name"]]
rows = []
passed_tests = 0
for index, test in enumerate(payload["tests"]):
    try:
        observed = fn(*test.get("args", []), **test.get("kwargs", {}))
        expected = test["expected"]
        passed = observed == expected
        if passed:
            passed_tests += 1
        rows.append({
            "index": index, "passed": passed,
            "expected": expected, "observed": observed,
        })
    except Exception as exc:
        rows.append({
            "index": index, "passed": False,
            "expected": test["expected"],
            "error": type(exc).__name__ + ": " + str(exc)[:160],
        })
print(json.dumps({
    "passed": passed_tests == len(payload["tests"]),
    "passed_tests": passed_tests,
    "total_tests": len(payload["tests"]),
    "tests": rows,
}, sort_keys=True, default=str))
'''
