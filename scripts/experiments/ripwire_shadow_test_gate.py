#!/usr/bin/env python3
"""Read-only intake of ripwire --json --test-gate advisory output.

This module NEVER executes ripwire, tests, shells, or models. It verifies
bounded, local test-file candidates and reports risk; the report cannot grant
claim/admission, change a harness, or waive broader regression testing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

MAX_BYTES = 512_000
MAX_TESTS = 500
MAX_COUNT = 2_000_000
ID_SHA = re.compile(r"[0-9a-f]{40}\Z")
SAFE_PATH = re.compile(r"[A-Za-z0-9_./-]{1,240}\Z")


class UnsafeEvidence(ValueError):
    pass


def count(obj: dict, key: str) -> int:
    value = obj.get(key)
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise UnsafeEvidence("missing or malformed numeric field")
    return value


def flag(obj: dict, key: str) -> bool:
    value = obj.get(key)
    if type(value) is not bool:
        raise UnsafeEvidence("missing or malformed boolean field")
    return value


def local_test(root: Path, name: str) -> str:
    if type(name) is not str or SAFE_PATH.fullmatch(name) is None:
        raise UnsafeEvidence("unsafe test name")
    parts = PurePosixPath(name).parts
    if not parts or parts[0] not in ("tests", "test") or ".." in parts or "." in parts:
        raise UnsafeEvidence("test not in bounded test directory")
    if not name.endswith((".py", ".sh")):
        raise UnsafeEvidence("unsupported suggested test extension")
    path = root.joinpath(*parts)
    if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
        raise UnsafeEvidence("symlink in test path")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise UnsafeEvidence("test path missing or escapes root")
    return PurePosixPath(*parts).as_posix()


def review(data: dict, root: Path, *, source_sha: str,
           include_local_paths: bool = False) -> dict:
    if type(data) is not dict or data.get("root") != ".":
        raise UnsafeEvidence("unsupported report root")
    if type(source_sha) is not str or ID_SHA.fullmatch(source_sha) is None:
        raise UnsafeEvidence("source checkout pin missing")
    if not root.is_absolute() or not root.is_dir() or not (root / ".git").exists():
        raise UnsafeEvidence("absolute git checkout required")
    root = root.resolve(strict=True)
    tests = count(data, "tests")
    shown_tests = count(data, "shown_tests")
    untested = count(data, "untested")
    impacted = count(data, "impacted")
    unresolved = count(data, "graph_unresolved")
    ambiguous = count(data, "graph_ambiguous")
    unindexed = count(data, "graph_unindexed")
    declined = count(data, "declined_calls")
    dynamic = count(data, "script_gates_unresolved_dynamic")
    unmodelled = count(data, "script_gates_unmodelled")
    tests_capped = flag(data, "tests_capped")
    untested_capped = flag(data, "untested_capped")
    if not flag(data, "counts_floor"):
        raise UnsafeEvidence("source did not declare conservative count semantics")
    if tests > MAX_TESTS or shown_tests > MAX_TESTS or shown_tests > tests:
        raise UnsafeEvidence("test selection exceeds bounds")
    rows = data.get("tests_to_run")
    if type(rows) is not list or len(rows) > MAX_TESTS:
        raise UnsafeEvidence("malformed suggestions")
    paths: list[str] = []
    runner_unknown = 0
    for row in rows:
        if type(row) is not dict:
            raise UnsafeEvidence("non-object test recommendation")
        val = row.get("p")
        if type(val) is str:
            candidates = [val]
            if row.get("n") is not None:
                raise UnsafeEvidence("unexpected grouped count")
        elif type(val) is list:
            candidates = val
            if type(row.get("n")) is not int or row["n"] != len(candidates) or not 1 <= len(candidates) <= 16:
                raise UnsafeEvidence("invalid test grouping")
        else:
            raise UnsafeEvidence("missing suggested test")
        for candidate in candidates:
            paths.append(local_test(root, candidate))
        if row.get("run_unknown", False) is not False and type(row.get("run_unknown")) is not bool:
            raise UnsafeEvidence("invalid runner unknown flag")
        if row.get("run_unknown", False) is True:
            runner_unknown += len(candidates)
        # Deliberately ignore arbitrary upstream run/next commands; never execute.
    if len(paths) != shown_tests or len(set(paths)) != len(paths):
        raise UnsafeEvidence("reported/actual test count mismatch or duplicate")
    if tests_capped is False and shown_tests != tests:
        raise UnsafeEvidence("uncapped report omitted tests")
    if tests == 0 and impacted == 0 and untested == 0:
        raise UnsafeEvidence("empty gate is not evidence of tested source")
    digest = hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
    incomplete = bool(
        tests_capped or untested_capped or untested or ambiguous or
        unresolved or unindexed or declined or dynamic or unmodelled
    )
    out = {
        "kind": "ripwire_shadow_test_gate_v1",
        "upstream_repo": "redhat-et/ripwire",
        "checkout_commit_claimed": source_sha,
        "suggested_test_count": len(paths),
        "reported_total_tests_floor": tests,
        "reported_impacted_symbols_floor": impacted,
        "reported_untested_symbols_floor": untested,
        "graph_ambiguous": ambiguous,
        "graph_unresolved": unresolved,
        "graph_unindexed": unindexed,
        "declined_calls": declined,
        "unknown_runner_test_count": runner_unknown,
        "test_list_sha256": digest,
        "coverage_complete": False,
        "selection_incomplete_or_uncertain": incomplete,
        "full_test_suite_still_required": True,
        "source_identity_independently_verified": False,
        "test_coverage_independently_verified": False,
        "suggestions_executed": False,
        "dispatch_allowed": False,
        "harness_change_allowed": False,
        "production_gate": "HOLD",
        "note": "Candidate tests only. Static call-graph links are incomplete; no commands or tests were run.",
    }
    if include_local_paths:
        out["verified_local_test_paths"] = sorted(paths)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--show-local-paths", action="store_true")
    args = parser.parse_args(argv)
    try:
        f = args.json_report
        if not f.is_file() or f.stat().st_size > MAX_BYTES:
            raise UnsafeEvidence("input file unavailable or oversize")
        raw = json.loads(f.read_text("utf-8"),
                         parse_constant=lambda _: (_ for _ in ()).throw(UnsafeEvidence("nonfinite JSON")))
        result = review(raw, args.source_root, source_sha=args.source_sha,
                        include_local_paths=args.show_local_paths)
    except (OSError, UnicodeError, ValueError, RecursionError):
        print("ripwire test hints: malformed or unverified input refused", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
