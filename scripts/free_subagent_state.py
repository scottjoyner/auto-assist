"""Free subagent supervision slice — state management and lifecycle."""

from __future__ import annotations

import json
import os
import pathlib
from datetime import UTC, datetime
from typing import Any

DEFAULT_STATE_PATH = pathlib.Path("local_subagent_state.jsonl")
DEFAULT_FIXTURE_PATH = pathlib.Path("tests/fixtures/openrouter_models_sample.jsonl")
DEFAULT_PROVIDER_POOLS_FIXTURE_PATH = pathlib.Path(
    "tests/fixtures/provider_pools_sample.jsonl"
)


def credential_present() -> bool:
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPENROUTER_API_KEY_2"):
        if os.environ.get(key):
            return True
    return False


def load_state(state_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    path = state_path or DEFAULT_STATE_PATH
    records: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
    return records


def save_state(
    records: list[dict[str, Any]], state_path: pathlib.Path | None = None
) -> None:
    path = state_path or DEFAULT_STATE_PATH
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(
                json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )


def register_subagent(
    record: dict[str, Any], state_path: pathlib.Path | None = None
) -> list[dict[str, Any]]:
    from free_subagent_routing import route_kind

    # Observational records are not authority. Never accept a caller-owned
    # claimed "admitted" status as an authenticated grant; reject before I/O.
    if not isinstance(record, dict):
        raise ValueError("invalid_observation_record")
    # Reject caller-owned authority assertions; observational JSON is never
    # authenticated issuance, even when the caller chooses a believable model.
    if str(record.get("status", "")).strip().lower() == "admitted" or any(
        key in record for key in ("admitted", "dispatch_authorized", "execution_authorized",
                                 "quota_authorized")
    ):
        raise ValueError("admission_state_requires_resource_owned_authority")

    records = load_state(state_path)
    record = dict(record)
    for k in ("title", "objective", "model", "worktree", "pid", "session", "status"):
        if k not in record:
            record[k] = ""

    requested = str(record.get("requested_model") or record.get("model") or "")
    resolved = str(record.get("resolved_model") or "").strip() or None
    kind = route_kind(requested)
    if kind == "alias" and not resolved and not record.get("allow_unresolved_alias"):
        raise ValueError(
            "router alias lacks resolved_model; use an exact model ID for real "
            "subagent work or explicitly mark a qualification canary"
        )

    record["requested_model"] = requested
    record["resolved_model"] = resolved
    record["route_kind"] = kind
    if resolved:
        record["effective_model"] = resolved
        record["model_attribution"] = "resolved-upstream"
        record["model_identity_complete"] = True
    elif requested and kind == "exact":
        record["effective_model"] = requested
        record["model_attribution"] = "exact-request"
        record["model_identity_complete"] = True
    else:
        record["effective_model"] = None
        record["model_attribution"] = "unresolved-router-alias"
        record["model_identity_complete"] = False

    if "created_at" not in record:
        record["created_at"] = _now_utc()
    record["updated_at"] = _now_utc()
    records.append(record)
    save_state(records, state_path)
    return records


def inspect_subagents(state_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    return load_state(state_path)


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")