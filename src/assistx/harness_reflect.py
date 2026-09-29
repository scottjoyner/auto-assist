"""Harness reflect: turn a benchmark fail set into structured correction pairs
and harness-fix proposals via a reasoner endpoint.

This is the reflect step of the harness evolution cycle (see the harness
evolution cycle design in the knowledge base). A benchmark run artifact in,
a validated reflection out — the module is pure (no I/O); callers own task
creation, LLM execution, and evidence persistence.

Run artifact schema (produced by the agent-harness benchmark CLI / rescores):

    {
      "suite_id": "cpm-tb2-bench-v1",
      "harness_id": "cpm-tb2-bench-v1",
      "endpoint": "optiplex:1235",
      "model_key": "minicpm5-2b-iter5",
      "results": [
        {"task_id": "BM-007", "passed": false,
         "expected": "...", "actual": "...", "duration_s": 12.3},
        ...
      ]
    }

Reflection schema (what the model must return, JSON):

    {
      "correction_pairs": [
        {"fail_id": "BM-007", "prompt": "...", "completion": "..."}
      ],
      "harness_fix_proposals": [
        {"fail_id": "BM-008", "title": "...", "patch_summary": "..."}
      ],
      "notes": "optional"
    }
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

REFLECT_TASK_KIND = "harness_reflect"
MAX_FAILS_PER_TASK = 24
MAX_FIELD_CHARS = 2000
MAX_COMPLETION_CHARS = 4000

REFLECTION_CONSTRAINTS = (
    "Correction pairs are canonical tool-first answers: prompt must be the "
    "corrected instruction or tool call for the failed task; completion must be "
    "the exact corrected output, verified working — no preamble, no reasoning "
    "narrative. Harness-fix proposals stay bounded changes to the harness, not "
    "the model. Do not invent tasks outside the fail set. Do not propose "
    "promotion; promotion is operator-owned."
)


def _text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    return str(value or "")[:limit]


def run_identity(run: dict[str, Any]) -> str:
    """Stable identity for a run artifact (suite + endpoint + result digest)."""
    digest_input = json.dumps(
        {
            "suite_id": run.get("suite_id"),
            "harness_id": run.get("harness_id"),
            "endpoint": run.get("endpoint"),
            "results": run.get("results") or [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(digest_input.encode()).hexdigest()[:16]


def fail_set_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Failed tasks from a run artifact, bounded for a reflect payload."""
    results = run.get("results")
    if not isinstance(results, list):
        raise ValueError("run artifact must carry a results list")
    fails: list[dict[str, Any]] = []
    for entry in results:
        if not isinstance(entry, dict) or entry.get("passed") is not False:
            continue
        task_id = _text(entry.get("task_id") or entry.get("id"), 64)
        if not task_id:
            continue
        fails.append(
            {
                "task_id": task_id,
                "expected": _text(entry.get("expected")),
                "actual": _text(entry.get("actual")),
                "duration_s": entry.get("duration_s"),
            }
        )
        if len(fails) >= MAX_FAILS_PER_TASK:
            break
    return fails


def build_reflect_prompt(run: dict[str, Any], fail_set: list[dict[str, Any]]) -> str:
    return (
        "You are reflecting on failed tasks from an agent-harness benchmark run.\n"
        f"Suite: {run.get('suite_id')}\n"
        f"Harness: {run.get('harness_id')}\n"
        f"Endpoint: {run.get('endpoint')}\n"
        f"Model: {run.get('model_key')}\n\n"
        f"Fail set ({len(fail_set)} tasks, JSON):\n"
        f"{json.dumps(fail_set, indent=2)}\n\n"
        f"Produce ONE JSON object with keys 'correction_pairs' and "
        f"'harness_fix_proposals' (and optional 'notes'). Every entry must "
        f"reference its fail task_id.\n\nConstraints: {REFLECTION_CONSTRAINTS}\n\n"
        "Return only the JSON object."
    )


def build_reflect_task(
    run: dict[str, Any],
    *,
    target_agent_id: str,
    priority: str = "BATCH",
) -> dict[str, Any]:
    """AssistX task for the reflect step, addressed to a reasoner endpoint."""
    fail_set = fail_set_from_run(run)
    if not fail_set:
        raise ValueError("run artifact has no failed tasks to reflect on")
    identity = run_identity(run)
    return {
        "title": f"Harness reflect {run.get('suite_id')} on {run.get('endpoint')}",
        "kind": REFLECT_TASK_KIND,
        "required_capabilities": ["llm"],
        "target_agent_id": target_agent_id,
        "priority": priority,
        "preemptible": True,
        "max_migrations": 2,
        "idempotency_key": f"harness-reflect:{identity}",
        "payload": {
            "queue_class": "batch",
            "harness_reflect": True,
            "suite_id": run.get("suite_id"),
            "harness_id": run.get("harness_id"),
            "endpoint": run.get("endpoint"),
            "model_key": run.get("model_key"),
            "run_identity": identity,
            "fail_set": fail_set,
            "constraints": REFLECTION_CONSTRAINTS,
            "prompt": build_reflect_prompt(run, fail_set),
            "deadline_seconds": 900,
            "max_tokens": 4096,
            "allow_model_load": False,
        },
    }


def extract_reflection(text: str) -> dict[str, Any] | None:
    """Parse the reflection JSON out of model output. Tolerates code fences
    and surrounding prose; returns None when no JSON object is present."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    # Balanced-brace scan for the first complete JSON object.
    depth, start = 0, -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except Exception:
                    start = -1
    return None


def validate_reflection(
    reflection: Any,
    fail_set: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Validate a reflection against the fail set.

    Returns ``(errors, uncovered)``: schema/reference violations, and the fail
    ids no correction pair or proposal covers."""
    errors: list[str] = []
    if not isinstance(reflection, dict):
        return ["reflection must be a JSON object"], [
            f["task_id"] for f in fail_set
        ]
    fail_ids = {f["task_id"] for f in fail_set}
    pairs = reflection.get("correction_pairs")
    proposals = reflection.get("harness_fix_proposals")
    if not isinstance(pairs, list):
        errors.append("correction_pairs must be a list")
        pairs = []
    if not isinstance(proposals, list):
        errors.append("harness_fix_proposals must be a list")
        proposals = []
    if not pairs and not proposals:
        errors.append("reflection must contain at least one correction pair or proposal")

    covered: set[str] = set()
    for index, pair in enumerate(pairs):
        where = f"correction_pairs[{index}]"
        if not isinstance(pair, dict):
            errors.append(f"{where} must be an object")
            continue
        fail_id = _text(pair.get("fail_id"), 64)
        if fail_id not in fail_ids:
            errors.append(f"{where} references unknown fail_id {fail_id!r}")
            continue
        prompt = _text(pair.get("prompt"))
        completion = _text(pair.get("completion"), MAX_COMPLETION_CHARS)
        if not prompt:
            errors.append(f"{where} is missing prompt")
        if not completion:
            errors.append(f"{where} is missing completion")
        elif len(str(pair.get("completion") or "")) > MAX_COMPLETION_CHARS:
            errors.append(f"{where} completion exceeds {MAX_COMPLETION_CHARS} chars")
        else:
            covered.add(fail_id)
    for index, proposal in enumerate(proposals):
        where = f"harness_fix_proposals[{index}]"
        if not isinstance(proposal, dict):
            errors.append(f"{where} must be an object")
            continue
        fail_id = _text(proposal.get("fail_id"), 64)
        if fail_id not in fail_ids:
            errors.append(f"{where} references unknown fail_id {fail_id!r}")
            continue
        if not _text(proposal.get("title")):
            errors.append(f"{where} is missing title")
        elif not _text(proposal.get("patch_summary")):
            errors.append(f"{where} is missing patch_summary")
        else:
            covered.add(fail_id)
    uncovered = sorted(fail_ids - covered)
    return errors, uncovered
