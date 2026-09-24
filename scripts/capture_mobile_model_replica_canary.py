#!/usr/bin/env python3
"""Capture operator evidence for the Kipnerter Fleet replica-loss canary.

This tool is deliberately read-only with respect to fleet/runtime state. It:
- reads the authoritative AssistX mobile catalog;
- sends one plain Fleet-model probe through the existing mobile route;
- captures the opaque kmr:* response correlation ID;
- reads the matching Auto-Router decision + completed execution from its local
  SQLite outbox;
- writes validator-ready before/after evidence; and
- runs the checked-in validator automatically after the after phase.

It never changes projection, admission, routing policy, model selection, or a
runtime process. The operator performs any approved replica eligibility change
between the before and after phases.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable


class CaptureError(RuntimeError):
    pass


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalize_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise CaptureError("AssistX base URL must use http:// or https://")
    return url


def _headers_from_env(specs: Iterable[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for spec in specs:
        if "=" not in spec:
            raise CaptureError(
                f"invalid --header-env {spec!r}; expected Header-Name=ENV_VAR"
            )
        name, env_name = (part.strip() for part in spec.split("=", 1))
        if not name or not env_name:
            raise CaptureError(
                f"invalid --header-env {spec!r}; expected Header-Name=ENV_VAR"
            )
        if name.lower() == "tailscale-user-login":
            raise CaptureError(
                "refusing to synthesize Tailscale-User-Login; use the real "
                "authenticated Tailscale/Serve path"
            )
        value = os.environ.get(env_name)
        if value is None or not value:
            raise CaptureError(f"required environment variable {env_name!r} is unset")
        headers[name] = value
    return headers


def _http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[dict[str, Any], dict[str, str]]:
    request_headers = {
        "Accept": "application/json",
        **(headers or {}),
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=url,
        data=data,
        headers=request_headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            response_headers = {
                str(key).lower(): str(value)
                for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise CaptureError(
            f"{method.upper()} {url} returned HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise CaptureError(
            f"{method.upper()} {url} failed: {exc.reason}"
        ) from exc

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"{method.upper()} {url} returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise CaptureError(f"{method.upper()} {url} returned a non-object JSON body")
    return decoded, response_headers


def _catalog_models(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    models = catalog.get("models")
    if not isinstance(models, list):
        raise CaptureError("runtime catalog is missing models")
    return [item for item in models if isinstance(item, dict)]


def _ready_count(model: dict[str, Any]) -> int:
    try:
        return int(model.get("ready_runtime_count"))
    except (TypeError, ValueError) as exc:
        raise CaptureError("catalog model has invalid ready_runtime_count") from exc


def _select_catalog_model(
    catalog: dict[str, Any],
    *,
    display_name: str | None,
    handle: str | None,
    minimum_ready: int,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for model in _catalog_models(catalog):
        if handle is not None and model.get("model_handle") != handle:
            continue
        if display_name is not None and model.get("display_name") != display_name:
            continue
        if model.get("state") != "ready":
            continue
        if _ready_count(model) < minimum_ready:
            continue
        matches.append(model)

    if not matches:
        target = handle or display_name or "<unspecified>"
        raise CaptureError(
            f"no ready catalog model matches {target!r} with "
            f"ready_runtime_count >= {minimum_ready}"
        )
    if len(matches) > 1:
        labels = [
            f"{item.get('display_name')}:{item.get('model_handle')}"
            for item in matches
        ]
        raise CaptureError(
            "catalog target is ambiguous; pass --handle. Matches: "
            + ", ".join(labels)
        )

    selected = matches[0]
    selected_handle = selected.get("model_handle")
    if (
        not isinstance(selected_handle, str)
        or not selected_handle.startswith("model:v1:")
    ):
        raise CaptureError("selected catalog row lacks an opaque model:v1 handle")
    return selected


def _outbox_connection(database_path: Path) -> sqlite3.Connection:
    if not database_path.exists():
        raise CaptureError(f"Auto-Router outbox database does not exist: {database_path}")
    uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise CaptureError(f"failed to open Auto-Router outbox read-only: {exc}") from exc
    connection.row_factory = sqlite3.Row
    return connection


def _find_event_once(
    database_path: Path,
    *,
    event_type: str,
    mobile_request_id: str,
    scan_limit: int = 2000,
) -> dict[str, Any] | None:
    with _outbox_connection(database_path) as connection:
        try:
            rows = connection.execute(
                """
                SELECT event_id, event_type, source_service, payload_json,
                       status, created_at, updated_at
                FROM event_outbox
                WHERE event_type = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (event_type, scan_limit),
            ).fetchall()
        except sqlite3.Error as exc:
            raise CaptureError(f"failed to read Auto-Router outbox: {exc}") from exc

    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("assistx_mobile_request_id") != mobile_request_id:
            continue
        return {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "source_service": row["source_service"],
            "payload": payload,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    return None


def _wait_for_event(
    database_path: Path,
    *,
    event_type: str,
    mobile_request_id: str,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        event = _find_event_once(
            database_path,
            event_type=event_type,
            mobile_request_id=mobile_request_id,
        )
        if event is not None:
            return event
        if time.monotonic() >= deadline:
            raise CaptureError(
                f"timed out waiting for {event_type} matching {mobile_request_id}"
            )
        time.sleep(poll_seconds)


def _probe_payload(handle: str, *, phase: str) -> dict[str, Any]:
    return {
        "model_handle": handle,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Replica canary {phase}-state probe. "
                    "Reply with exactly OK."
                ),
            }
        ],
        "stream": False,
        "temperature": 0,
        "max_tokens": 16,
    }


def _validator_module() -> Any:
    script = Path(__file__).resolve().with_name(
        "validate_mobile_model_replica_canary.py"
    )
    spec = importlib.util.spec_from_file_location(
        "validate_mobile_model_replica_canary",
        script,
    )
    if spec is None or spec.loader is None:
        raise CaptureError(f"cannot load validator {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state_path(out_dir: Path) -> Path:
    return out_dir / "capture-state.json"


def _phase_path(out_dir: Path, phase: str, suffix: str) -> Path:
    return out_dir / f"{phase}-{suffix}.json"


def _load_state(out_dir: Path) -> dict[str, Any]:
    path = _state_path(out_dir)
    if not path.exists():
        raise CaptureError(
            f"missing {path}; capture the before phase first"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaptureError(f"invalid capture state JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CaptureError("capture state must be a JSON object")
    return value


def _build_evidence_bundle(out_dir: Path) -> dict[str, Any]:
    bundle: dict[str, Any] = {}
    for phase in ("before", "after"):
        values: dict[str, Any] = {}
        for key, suffix in (
            ("catalog", "catalog"),
            ("mobile_response", "response"),
            ("route_decision_event", "route-decision"),
            ("route_execution_event", "route-execution"),
        ):
            path = _phase_path(out_dir, phase, suffix)
            if not path.exists():
                raise CaptureError(f"missing phase evidence: {path}")
            values[key] = json.loads(path.read_text(encoding="utf-8"))
        request_path = _phase_path(out_dir, phase, "request-metadata")
        request_metadata = json.loads(request_path.read_text(encoding="utf-8"))
        values["mobile_request_id"] = request_metadata["mobile_request_id"]
        bundle[phase] = values
    return bundle


def capture_phase(
    *,
    phase: str,
    assistx_base_url: str,
    router_db: Path,
    out_dir: Path,
    display_name: str | None,
    handle: str | None,
    request_headers: dict[str, str],
    event_timeout_seconds: float,
    assistx_sha: str | None,
    router_sha: str | None,
) -> dict[str, Any]:
    if phase not in {"before", "after"}:
        raise CaptureError(f"unsupported phase {phase!r}")

    out_dir.mkdir(parents=True, exist_ok=True)
    base_url = _normalize_base_url(assistx_base_url)

    if phase == "before" and _state_path(out_dir).exists():
        raise CaptureError(
            "output directory already contains capture-state.json; "
            "use a fresh --out-dir so prior evidence cannot be overwritten"
        )

    prior_state: dict[str, Any] | None = None
    if phase == "after":
        prior_state = _load_state(out_dir)
        state_handle = prior_state.get("model_handle")
        if not isinstance(state_handle, str):
            raise CaptureError("before capture state lacks model_handle")
        if handle is not None and handle != state_handle:
            raise CaptureError(
                "--handle does not match the before-phase model handle"
            )
        handle = state_handle
        if display_name is None:
            state_name = prior_state.get("display_name")
            display_name = state_name if isinstance(state_name, str) else None
        for key, current in (
            ("auto_assist_sha", assistx_sha),
            ("auto_router_sha", router_sha),
        ):
            before_value = prior_state.get(key)
            if before_value and current and before_value != current:
                raise CaptureError(
                    f"{key} changed between before and after capture: "
                    f"{before_value} != {current}"
                )

    catalog, _ = _http_json(
        "GET",
        f"{base_url}/api/v1/runtime/catalog",
        headers=request_headers,
    )
    minimum_ready = 2 if phase == "before" else 1
    selected = _select_catalog_model(
        catalog,
        display_name=display_name,
        handle=handle,
        minimum_ready=minimum_ready,
    )
    selected_handle = str(selected["model_handle"])
    ready_count = _ready_count(selected)

    if phase == "after" and prior_state is not None:
        before_count = int(prior_state["ready_runtime_count"])
        if ready_count >= before_count:
            raise CaptureError(
                "after catalog does not prove replica loss: "
                f"before={before_count}, after={ready_count}"
            )

    response, response_headers = _http_json(
        "POST",
        f"{base_url}/api/v1/model/chat/completions",
        body=_probe_payload(selected_handle, phase=phase),
        headers=request_headers,
        timeout_seconds=300.0,
    )
    if response.get("model") != selected_handle:
        raise CaptureError(
            "mobile response model handle does not match selected handle"
        )

    mobile_request_id = response_headers.get("x-kipnerter-model-request-id")
    if (
        not isinstance(mobile_request_id, str)
        or not mobile_request_id.startswith("kmr:")
    ):
        raise CaptureError(
            "AssistX response lacks X-Kipnerter-Model-Request-ID"
        )

    decision = _wait_for_event(
        router_db,
        event_type="router.route_decision",
        mobile_request_id=mobile_request_id,
        timeout_seconds=event_timeout_seconds,
    )
    execution = _wait_for_event(
        router_db,
        event_type="router.execution_stage.completed",
        mobile_request_id=mobile_request_id,
        timeout_seconds=event_timeout_seconds,
    )
    execution_payload = execution.get("payload", {})
    serving_provider = (
        execution_payload.get("provider_id")
        or execution_payload.get("provider")
    )
    if not isinstance(serving_provider, str) or not serving_provider:
        raise CaptureError("completed execution event lacks serving provider")

    _json_write(_phase_path(out_dir, phase, "catalog"), catalog)
    _json_write(_phase_path(out_dir, phase, "response"), response)
    _json_write(_phase_path(out_dir, phase, "response-headers"), response_headers)
    _json_write(_phase_path(out_dir, phase, "route-decision"), decision)
    _json_write(_phase_path(out_dir, phase, "route-execution"), execution)
    _json_write(
        _phase_path(out_dir, phase, "request-metadata"),
        {
            "phase": phase,
            "captured_at_unix": int(time.time()),
            "model_handle": selected_handle,
            "display_name": selected.get("display_name"),
            "ready_runtime_count": ready_count,
            "mobile_request_id": mobile_request_id,
            "auto_assist_sha": assistx_sha,
            "auto_router_sha": router_sha,
        },
    )

    state = {
        "model_handle": selected_handle,
        "display_name": selected.get("display_name"),
        "ready_runtime_count": (
            int(prior_state["ready_runtime_count"])
            if phase == "after" and prior_state is not None
            else ready_count
        ),
        "before_mobile_request_id": (
            prior_state.get("before_mobile_request_id")
            if prior_state is not None
            else mobile_request_id
        ),
        "before_serving_provider": (
            prior_state.get("before_serving_provider")
            if prior_state is not None
            else serving_provider
        ),
        "before_serving_node_id": (
            prior_state.get("before_serving_node_id")
            if prior_state is not None
            else (
                execution_payload.get("runtime_node_id")
                or execution_payload.get("node_id")
            )
        ),
        "auto_assist_sha": assistx_sha
        or (prior_state or {}).get("auto_assist_sha"),
        "auto_router_sha": router_sha
        or (prior_state or {}).get("auto_router_sha"),
    }
    _json_write(_state_path(out_dir), state)

    execution_payload = execution.get("payload", {})
    summary: dict[str, Any] = {
        "phase": phase,
        "model_handle": selected_handle,
        "display_name": selected.get("display_name"),
        "ready_runtime_count": ready_count,
        "mobile_request_id": mobile_request_id,
        "route_profile": decision.get("payload", {}).get("profile"),
        "serving_provider": serving_provider,
        "serving_node_id": (
            execution_payload.get("runtime_node_id")
            or execution_payload.get("node_id")
        ),
        "runtime_instance_id": execution_payload.get("runtime_instance_id"),
        "runtime_kind": execution_payload.get("runtime_kind"),
        "artifact_fingerprint": execution_payload.get("artifact_fingerprint"),
        "out_dir": str(out_dir),
    }
    if phase == "before":
        summary["transition_target_provider"] = serving_provider

    if phase == "after":
        bundle = _build_evidence_bundle(out_dir)
        _json_write(out_dir / "evidence.json", bundle)
        validator = _validator_module()
        try:
            result = validator.validate_evidence(bundle)
        except validator.CanaryEvidenceError as exc:
            _json_write(
                out_dir / "result.json",
                {"result": "FAIL", "error": str(exc)},
            )
            raise CaptureError(f"replica canary validation failed: {exc}") from exc
        _json_write(out_dir / "result.json", result)
        summary["validation"] = result

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture read-only Kipnerter Fleet replica-canary evidence."
    )
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument(
        "--assistx-base-url",
        default=os.environ.get("ASSISTX_BASE_URL"),
    )
    parser.add_argument(
        "--router-db",
        type=Path,
        default=(
            Path(os.environ["ROUTER_DB"])
            if os.environ.get("ROUTER_DB")
            else None
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("artifacts/kipnerter-replica-canary"),
    )
    parser.add_argument(
        "--display-name",
        default=os.environ.get("DISPLAY_NAME"),
    )
    parser.add_argument("--handle")
    parser.add_argument(
        "--header-env",
        action="append",
        default=[],
        metavar="HEADER=ENV_VAR",
        help="read an optional request header value from an environment variable",
    )
    parser.add_argument(
        "--event-timeout-seconds",
        type=float,
        default=20.0,
    )
    parser.add_argument(
        "--assistx-sha",
        default=os.environ.get("ASSISTX_SHA"),
    )
    parser.add_argument(
        "--router-sha",
        default=os.environ.get("AUTO_ROUTER_SHA"),
    )
    args = parser.parse_args()

    if not args.assistx_base_url:
        parser.error("--assistx-base-url or ASSISTX_BASE_URL is required")
    if args.router_db is None:
        parser.error("--router-db or ROUTER_DB is required")
    if args.phase == "before" and not (args.display_name or args.handle):
        parser.error("before phase requires --display-name/DISPLAY_NAME or --handle")
    if not args.assistx_sha:
        parser.error("--assistx-sha or ASSISTX_SHA is required for exact-head evidence")
    if not args.router_sha:
        parser.error("--router-sha or AUTO_ROUTER_SHA is required for exact-head evidence")

    try:
        summary = capture_phase(
            phase=args.phase,
            assistx_base_url=args.assistx_base_url,
            router_db=args.router_db,
            out_dir=args.out_dir,
            display_name=args.display_name,
            handle=args.handle,
            request_headers=_headers_from_env(args.header_env),
            event_timeout_seconds=args.event_timeout_seconds,
            assistx_sha=args.assistx_sha,
            router_sha=args.router_sha,
        )
    except CaptureError as exc:
        print(json.dumps({"result": "FAIL", "error": str(exc)}, indent=2))
        return 1

    print(json.dumps({"result": "OK", **summary}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
