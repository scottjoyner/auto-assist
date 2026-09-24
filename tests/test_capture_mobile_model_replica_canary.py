from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_mobile_model_replica_canary.py"
_SPEC = importlib.util.spec_from_file_location("capture_mobile_model_replica_canary", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

CaptureError = _MODULE.CaptureError
_build_evidence_bundle = _MODULE._build_evidence_bundle
_find_event_once = _MODULE._find_event_once
_headers_from_env = _MODULE._headers_from_env
_select_catalog_model = _MODULE._select_catalog_model


HANDLE = "model:v1:" + "a" * 32


def _catalog(count: int = 2) -> dict:
    return {
        "models": [
            {
                "model_handle": HANDLE,
                "display_name": "Ternary Bonsai 2",
                "state": "ready",
                "ready_runtime_count": count,
            },
            {
                "model_handle": "model:v1:" + "b" * 32,
                "display_name": "Other",
                "state": "ready",
                "ready_runtime_count": 1,
            },
        ]
    }


def test_select_catalog_model_requires_replication_for_before() -> None:
    selected = _select_catalog_model(
        _catalog(2),
        display_name="Ternary Bonsai 2",
        handle=None,
        minimum_ready=2,
    )
    assert selected["model_handle"] == HANDLE

    with pytest.raises(CaptureError, match="ready_runtime_count >= 2"):
        _select_catalog_model(
            _catalog(1),
            display_name="Ternary Bonsai 2",
            handle=None,
            minimum_ready=2,
        )


def test_header_env_never_synthesizes_tailscale_identity(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN", "secret-value")
    assert _headers_from_env(["Authorization=TOKEN"]) == {
        "Authorization": "secret-value"
    }

    monkeypatch.setenv("LOGIN", "scott@example.com")
    with pytest.raises(CaptureError, match="refusing to synthesize"):
        _headers_from_env(["Tailscale-User-Login=LOGIN"])


def test_find_event_once_matches_exact_mobile_request_id(tmp_path: Path) -> None:
    db = tmp_path / "router.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            CREATE TABLE event_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                event_type TEXT,
                source_service TEXT,
                payload_json TEXT,
                status TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        for index, mobile_request_id in enumerate(("kmr:other", "kmr:target"), 1):
            connection.execute(
                """
                INSERT INTO event_outbox (
                    event_id, event_type, source_service, payload_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"event-{index}",
                    "router.execution_stage.completed",
                    "auto-router",
                    json.dumps(
                        {
                            "assistx_mobile_request_id": mobile_request_id,
                            "provider": f"runtime-{index}",
                        }
                    ),
                    "pending",
                    index,
                    index,
                ),
            )

    event = _find_event_once(
        db,
        event_type="router.execution_stage.completed",
        mobile_request_id="kmr:target",
    )

    assert event is not None
    assert event["event_id"] == "event-2"
    assert event["payload"]["provider"] == "runtime-2"


def test_build_evidence_bundle_uses_request_metadata_correlation(tmp_path: Path) -> None:
    for phase, request_id in (("before", "kmr:before"), ("after", "kmr:after")):
        values = {
            "catalog": {"models": []},
            "response": {"model": HANDLE},
            "route-decision": {"payload": {"profile": "exact_artifact"}},
            "route-execution": {"payload": {"status": "completed"}},
            "request-metadata": {"mobile_request_id": request_id},
        }
        for suffix, value in values.items():
            (tmp_path / f"{phase}-{suffix}.json").write_text(
                json.dumps(value),
                encoding="utf-8",
            )

    bundle = _build_evidence_bundle(tmp_path)

    assert bundle["before"]["mobile_request_id"] == "kmr:before"
    assert bundle["after"]["mobile_request_id"] == "kmr:after"
    assert bundle["before"]["route_decision_event"]["payload"]["profile"] == "exact_artifact"
    assert bundle["after"]["route_execution_event"]["payload"]["status"] == "completed"
