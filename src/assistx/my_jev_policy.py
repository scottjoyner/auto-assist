from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

DEFAULT_POLICY_URL = "http://my-jev:8088/v1/agent-policy"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _coerce_bool(
    value: Any,
    default: bool = False,
) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "verified",
        }
    if value is None:
        return default
    return bool(value)


def shadow_enabled() -> bool:
    return _env_bool(
        "MY_JEV_POLICY_SHADOW_ENABLED",
        False,
    )


def _metadata(intent: dict[str, Any]) -> dict[str, Any]:
    raw = intent.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, json.JSONDecodeError):
            pass
    metadata = intent.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _string_list(
    value: Any,
) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [
            str(item)
            for item in value
            if str(item).strip()
        ]
    if isinstance(value, str) and value.strip():
        return [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]
    return []


def build_policy_request(
    intent: dict[str, Any],
) -> dict[str, Any]:
    metadata = _metadata(intent)
    source = str(
        intent.get("source")
        or metadata.get("source")
        or "assistx"
    )
    active_work = _string_list(
        metadata.get("active_work")
        or metadata.get("active_tasks")
    )
    pending_approvals = _string_list(
        metadata.get("pending_approvals")
    )
    capabilities = _string_list(
        metadata.get("available_capabilities")
    ) or [
        "chat",
        "task_graph",
        "tools",
        "delegate",
    ]

    speaker_verified = _coerce_bool(
        metadata.get("speaker_verified"),
        default=False,
    )
    actions_allowed = _env_bool(
        "MY_JEV_SHADOW_ACTIONS_ALLOWED",
        False,
    )
    external_allowed = _env_bool(
        "MY_JEV_SHADOW_EXTERNAL_ACTIONS_ALLOWED",
        False,
    )
    privileged_allowed = _env_bool(
        "MY_JEV_SHADOW_PRIVILEGED_ACTIONS_ALLOWED",
        False,
    )

    state = {
        "utterance": str(
            intent.get("text") or ""
        ),
        "conversation_summary": str(
            metadata.get("conversation_summary")
            or metadata.get("context_summary")
            or ""
        ),
        "source": source,
        "speaker_id": str(
            metadata.get("speaker_id")
            or metadata.get("user_id")
            or metadata.get("speaker")
            or ""
        ),
        "speaker_verified": speaker_verified,
        "foreground": _coerce_bool(
            metadata.get("foreground"),
            default=True,
        ),
        "active_work": active_work,
        "pending_approvals": pending_approvals,
        "available_capabilities": capabilities,
        "available_tools": _string_list(
            metadata.get("available_tools")
        ),
        "actions_allowed": actions_allowed,
        "external_actions_allowed": external_allowed,
        "privileged_actions_allowed": privileged_allowed,
        "metadata": {
            "assistx_intent_id": str(
                intent.get("id") or ""
            ),
            "legacy_classification": str(
                intent.get("classification")
                or ""
            ),
            "legacy_policy_action": str(
                intent.get("policy_action")
                or metadata.get("policy_action")
                or ""
            ),
        },
    }
    constraints = {
        "speaker_verified": speaker_verified,
        "actions_allowed": actions_allowed,
        "local_writes_allowed": _env_bool(
            "MY_JEV_SHADOW_LOCAL_WRITES_ALLOWED",
            actions_allowed,
        ),
        "external_actions_allowed": external_allowed,
        "privileged_actions_allowed": privileged_allowed,
        "approval_gate_available": _env_bool(
            "MY_JEV_SHADOW_APPROVAL_GATE_AVAILABLE",
            True,
        ),
        "active_work": bool(active_work),
    }
    return {
        "state": state,
        "constraints": constraints,
    }


def request_policy_shadow(
    intent: dict[str, Any],
) -> dict[str, Any] | None:
    if not shadow_enabled():
        return None

    request_payload = build_policy_request(
        intent
    )
    timeout = max(
        0.05,
        float(
            os.getenv(
                "MY_JEV_POLICY_SHADOW_TIMEOUT_S",
                "0.75",
            )
        ),
    )
    url = os.getenv(
        "MY_JEV_POLICY_URL",
        DEFAULT_POLICY_URL,
    ).strip()

    response = requests.post(
        url,
        json=request_payload,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(
            "my-jev policy response must be a JSON object"
        )
    if body.get("contract") != "assistx-agent-policy-v1":
        raise ValueError(
            "unexpected my-jev policy contract"
        )

    metadata = _metadata(intent)
    return {
        "shadow": True,
        "recorded_at_ts": time.time(),
        "request": request_payload,
        "response": body,
        "legacy": {
            "classification": str(
                intent.get("classification")
                or ""
            ),
            "policy_action": str(
                intent.get("policy_action")
                or metadata.get("policy_action")
                or ""
            ),
        },
    }
