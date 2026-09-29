"""Traceability views for the harness evolution cycle: chains, live tasks,
mistakes, and reflections — the data behind the control-room evolution page.

Pure aggregation over Neo4j row shapes:
- EvaluationRun dicts from ``list_evaluation_runs`` (``metadata_json`` string)
- Task dicts from ``get_tasks_by_status`` (``payload_json`` string)

Everything is tolerant of missing fields; the page renders what exists.
"""

from __future__ import annotations

import json
from typing import Any

HARNESS_TASK_KINDS = (
    "harness_reflect",
    "harness_train",
    "harness_deploy",
    "harness_rescore",
    "adaptive_model_benchmark",
)
LIVE_STATUSES = ("READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "ERROR")


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _excerpt(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip().replace("\n", " ⏎ ")
    return text[:limit]


def _run_ts(run: dict[str, Any]) -> int:
    for key in ("created_at_ts", "updated_at_ts"):
        ts = run.get(key)
        if isinstance(ts, (int, float)):
            return int(ts)
    return 0


def _chains_from_runs(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    chains: dict[str, dict[str, Any]] = {}
    for run in runs or []:
        metadata = _json_dict(run.get("metadata_json"))
        if not metadata.get("harness_evolution"):
            continue
        chain_id = str(metadata.get("chain_id") or "")
        stage = str(metadata.get("stage") or "")
        if not chain_id or not stage:
            continue
        chain = chains.setdefault(
            chain_id,
            {
                "chain_id": chain_id,
                "suite_id": metadata.get("suite_id") or run.get("suite_id"),
                "endpoint": metadata.get("endpoint"),
                "model_key": metadata.get("model_key"),
                "base_run_identity": metadata.get("base_run_identity"),
                "stages": [],
                "updated_at_ts": 0,
            },
        )
        stage_entry: dict[str, Any] = {
            "stage": stage,
            "status": str(run.get("status") or ""),
            "created_at_ts": _run_ts(run),
            "run_id": run.get("id"),
        }
        score = run.get("score")
        if isinstance(score, (int, float)):
            stage_entry["score"] = round(float(score), 4)
        dataset_ref = metadata.get("dataset_ref")
        if dataset_ref:
            stage_entry["dataset_ref"] = dataset_ref
        if metadata.get("gate"):
            stage_entry["gate"] = metadata["gate"]
        chain["stages"].append(stage_entry)
        chain["updated_at_ts"] = max(chain["updated_at_ts"], stage_entry["created_at_ts"])
    for chain in chains.values():
        order = {stage: index for index, stage in enumerate(
            ("reflect", "train", "deploy", "rescore")
        )}
        chain["stages"].sort(key=lambda s: (order.get(s["stage"], 99), s["created_at_ts"]))
        rescores = [s for s in chain["stages"] if s["stage"] == "rescore"]
        chain["score"] = rescores[-1]["score"] if rescores else None
    return chains


def _live_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live: list[dict[str, Any]] = []
    for task in tasks or []:
        kind = str(task.get("kind") or "")
        if kind not in HARNESS_TASK_KINDS:
            continue
        payload = _json_dict(task.get("payload_json"))
        live.append(
            {
                "id": task.get("id"),
                "kind": kind,
                "objective": _excerpt(task.get("title"), 160),
                "status": str(task.get("status") or ""),
                "target": task.get("target_agent_id"),
                "endpoint": payload.get("endpoint"),
                "suite_id": payload.get("suite_id"),
                "chain_id": (payload.get("chain") or {}).get("chain_id"),
                "updated_at_ts": int(task.get("updated_at_ts") or 0),
                "response": _excerpt(
                    task.get("summary") or task.get("last_error")
                ),
            }
        )
    live.sort(key=lambda t: t["updated_at_ts"], reverse=True)
    return live


def _mistakes(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mistakes: fail-set items from reflect tasks, plus failed harness tasks.

    Each entry keeps the task and chain it came from, so the page can jump
    from a mistake back to the chain that owns it."""
    mistakes: list[dict[str, Any]] = []
    for task in tasks or []:
        kind = str(task.get("kind") or "")
        if kind not in HARNESS_TASK_KINDS:
            continue
        payload = _json_dict(task.get("payload_json"))
        chain_id = (payload.get("chain") or {}).get("chain_id")
        status = str(task.get("status") or "")
        for fail in payload.get("fail_set") or []:
            if not isinstance(fail, dict):
                continue
            mistakes.append(
                {
                    "source_task_id": task.get("id"),
                    "kind": kind,
                    "chain_id": chain_id,
                    "suite_id": payload.get("suite_id"),
                    "task_id": fail.get("task_id"),
                    "expected": _excerpt(fail.get("expected")),
                    "actual": _excerpt(fail.get("actual")),
                    "status": status,
                }
            )
        if status in {"FAILED", "ERROR"} and not payload.get("fail_set"):
            mistakes.append(
                {
                    "source_task_id": task.get("id"),
                    "kind": kind,
                    "chain_id": chain_id,
                    "suite_id": payload.get("suite_id"),
                    "task_id": task.get("title"),
                    "expected": "",
                    "actual": _excerpt(task.get("last_error") or task.get("summary")),
                    "status": status,
                }
            )
    return mistakes


def harness_evolution_snapshot(
    runs: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Everything the evolution page renders, in one snapshot."""
    chains = _chains_from_runs(runs or [])
    live = _live_tasks(tasks or [])
    mistakes = _mistakes(tasks or [])
    live_ids = {t["id"] for t in live if t.get("id")}
    for chain in chains.values():
        chain["has_live_task"] = any(
            t.get("chain_id") == chain["chain_id"] for t in live
        )
    return {
        "chains": sorted(
            chains.values(), key=lambda c: c["updated_at_ts"], reverse=True
        ),
        "live_tasks": live,
        "mistakes": mistakes,
        "task_count": len(live_ids),
        "chain_count": len(chains),
    }
