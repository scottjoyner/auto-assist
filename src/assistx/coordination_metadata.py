from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _clean_bool(value: Any) -> bool:
    return bool(value)


def _merge_metadata(
    existing: Mapping[str, Any] | None,
    *,
    request: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out = _json_dict(existing)

    def _merge_section(key: str, derived: Mapping[str, Any] | None) -> None:
        if derived is None:
            return
        merged = dict(derived)
        existing_section = _json_dict(out.get(key))
        merged.update(existing_section)
        out[key] = merged

    _merge_section("request", request)
    _merge_section("task", task)
    _merge_section("context", context)
    return out


def build_event_request_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    subject = _json_dict(event.get("subject"))
    privacy = _json_dict(event.get("privacy"))
    event_id = _clean_str(event.get("event_id") or event.get("idempotency_key"))
    return {
        "request_id": event_id,
        "request_kind": "event",
        "event_id": event_id,
        "event_type": _clean_str(event.get("event_type"), "unknown"),
        "source_repo": _clean_str(event.get("source_repo"), "unknown"),
        "source_service": _clean_str(event.get("source_service"), "unknown"),
        "node_id": _clean_str(event.get("node_id"), "unknown"),
        "occurred_at": _clean_str(event.get("occurred_at"), _now_iso()),
        "idempotency_key": _clean_str(event.get("idempotency_key"), event_id),
        "schema_version": _clean_str(event.get("schema_version"), "1.0"),
        "subject_kind": _clean_str(subject.get("kind"), "unknown"),
        "subject_id": _clean_str(subject.get("id"), event_id),
        "pii": _clean_bool(privacy.get("pii")),
        "privacy_class": _clean_str(privacy.get("privacy_class"), "unknown"),
        "retention_class": _clean_str(privacy.get("retention_class"), "unknown"),
    }


def build_task_candidate_request_metadata(task: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _json_dict(task.get("metadata"))
    request_id = _clean_str(task.get("task_id") or task.get("id") or metadata.get("source_task_id"))
    return {
        "request_id": request_id,
        "request_kind": "task_candidate",
        "task_id": request_id,
        "source_task_id": _clean_str(metadata.get("source_task_id"), request_id),
        "source_event_id": _clean_str(metadata.get("source_event_id")),
        "source_repo": _clean_str(metadata.get("source_repo"), "auto-assist"),
        "source_service": _clean_str(metadata.get("source_service"), "assistx-router"),
        "node_id": _clean_str(metadata.get("node_id"), "assistx-api"),
        "occurred_at": _clean_str(metadata.get("occurred_at"), _now_iso()),
        "projection_kind": _clean_str(metadata.get("projection_kind"), "backlog_candidate"),
        "privacy": _clean_str(task.get("privacy"), "unknown"),
        "priority": _clean_str(task.get("priority"), "background"),
        "queue": _clean_str(task.get("queue"), "background"),
        "model": _clean_str(task.get("model"), "auto/backlog-burn"),
        "local_only": _clean_bool(task.get("local_only")),
        "allow_cloud": _clean_bool(task.get("allow_cloud")),
        "sensitive": _clean_bool(task.get("sensitive")),
        "status": _clean_str(task.get("status"), "unknown"),
        "max_completion_tokens": int(task.get("max_completion_tokens") or 700),
    }


def build_task_candidate_task_metadata(task: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _json_dict(task.get("metadata"))
    return {
        "task_id": _clean_str(task.get("task_id") or task.get("id") or metadata.get("source_task_id")),
        "title": _clean_str(task.get("title"), "AssistX task"),
        "prompt": _clean_str(task.get("prompt") or task.get("description"), _clean_str(task.get("title"), "AssistX task")),
        "model": _clean_str(task.get("model"), "auto/backlog-burn"),
        "priority": _clean_str(task.get("priority"), "background"),
        "local_only": _clean_bool(task.get("local_only")),
        "allow_cloud": _clean_bool(task.get("allow_cloud")),
        "sensitive": _clean_bool(task.get("sensitive")),
        "max_completion_tokens": int(task.get("max_completion_tokens") or 700),
        "status": _clean_str(task.get("status"), "unknown"),
        "queue": _clean_str(task.get("queue"), "background"),
        "privacy": _clean_str(task.get("privacy"), "unknown"),
        "metadata": metadata,
    }


def build_event_metadata(event: Mapping[str, Any], existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _merge_metadata(
        existing,
        request=build_event_request_metadata(event),
        context={
            "contract": "assistx.event_metadata.v1",
            "source": "assistx",
        },
    )


def build_task_candidate_metadata(task: Mapping[str, Any], existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request = build_task_candidate_request_metadata(task)
    task_metadata = build_task_candidate_task_metadata(task)
    return _merge_metadata(
        existing,
        request=request,
        task=task_metadata,
        context={
            "contract": "assistx.task_candidate_metadata.v1",
            "source": "assistx",
        },
    )
