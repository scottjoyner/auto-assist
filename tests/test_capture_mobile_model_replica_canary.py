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



def test_capture_before_refuses_to_overwrite_existing_evidence(tmp_path: Path) -> None:
    (tmp_path / "capture-state.json").write_text(
        json.dumps({"model_handle": HANDLE}),
        encoding="utf-8",
    )

    with pytest.raises(CaptureError, match="fresh --out-dir"):
        _MODULE.capture_phase(
            phase="before",
            assistx_base_url="https://assistx.example",
            router_db=tmp_path / "router.sqlite3",
            out_dir=tmp_path,
            display_name="Ternary Bonsai 2",
            handle=None,
            request_headers={},
            event_timeout_seconds=1,
            assistx_sha="assistx-a",
            router_sha="router-a",
        )


def test_capture_after_rejects_exact_head_drift_before_network(tmp_path: Path) -> None:
    (tmp_path / "capture-state.json").write_text(
        json.dumps(
            {
                "model_handle": HANDLE,
                "display_name": "Ternary Bonsai 2",
                "ready_runtime_count": 2,
                "auto_assist_sha": "assistx-a",
                "auto_router_sha": "router-a",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CaptureError, match="auto_assist_sha changed"):
        _MODULE.capture_phase(
            phase="after",
            assistx_base_url="https://assistx.example",
            router_db=tmp_path / "router.sqlite3",
            out_dir=tmp_path,
            display_name=None,
            handle=None,
            request_headers={},
            event_timeout_seconds=1,
            assistx_sha="assistx-b",
            router_sha="router-a",
        )



def test_capture_before_retains_serving_node_and_transition_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = {"http": 0}

    def fake_http_json(method, url, *, body=None, headers=None, timeout_seconds=30.0):
        calls["http"] += 1
        if method == "GET":
            return _catalog(2), {}
        return (
            {
                "id": "reply",
                "object": "chat.completion",
                "model": HANDLE,
                "choices": [],
            },
            {"x-kipnerter-model-request-id": "kmr:before"},
        )

    def fake_wait_for_event(
        database_path,
        *,
        event_type,
        mobile_request_id,
        timeout_seconds,
        poll_seconds=0.25,
    ):
        assert mobile_request_id == "kmr:before"
        if event_type == "router.route_decision":
            return {
                "payload": {
                    "profile": "exact_artifact",
                    "assistx_mobile_request_id": mobile_request_id,
                }
            }
        return {
            "payload": {
                "provider": "runtime-a",
                "provider_id": "runtime-a",
                "runtime_node_id": "x1-370",
                "runtime_instance_id": "runtime-a-instance",
                "runtime_kind": "lmstudio",
                "artifact_fingerprint": "sha256:bonsai",
                "status": "completed",
                "status_code": 200,
                "assistx_mobile_request_id": mobile_request_id,
            }
        }

    monkeypatch.setattr(_MODULE, "_http_json", fake_http_json)
    monkeypatch.setattr(_MODULE, "_wait_for_event", fake_wait_for_event)

    summary = _MODULE.capture_phase(
        phase="before",
        assistx_base_url="https://assistx.example",
        router_db=tmp_path / "router.sqlite3",
        out_dir=tmp_path / "evidence",
        display_name="Ternary Bonsai 2",
        handle=None,
        request_headers={},
        event_timeout_seconds=1,
        assistx_sha="assistx-a",
        router_sha="router-a",
    )

    assert calls["http"] == 2
    assert summary["transition_target_provider"] == "runtime-a"
    assert summary["serving_provider"] == "runtime-a"
    assert summary["serving_node_id"] == "x1-370"
    assert summary["runtime_instance_id"] == "runtime-a-instance"
    assert summary["runtime_kind"] == "lmstudio"

    state = json.loads((tmp_path / "evidence" / "capture-state.json").read_text())
    assert state["before_serving_provider"] == "runtime-a"
    assert state["before_serving_node_id"] == "x1-370"
