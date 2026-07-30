from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .recovery_island import RecoveryIslandExecutor


def _http(
    method: str,
    url: str,
    *,
    auth: tuple[str, str] | None = None,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return response.status, {}
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return exc.code, raw
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("payload")
    if isinstance(value, dict):
        return value
    raw = task.get("payload_json")
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _executor(args: argparse.Namespace) -> RecoveryIslandExecutor:
    return RecoveryIslandExecutor(
        node_id=args.node_id,
        state_dir=args.state_dir,
        http=_http,
    )


def execute_task(
    task: dict[str, Any],
    executor: RecoveryIslandExecutor,
) -> dict[str, Any]:
    required = {str(value) for value in task.get("required_capabilities") or []}
    payload = _task_payload(task)
    if "recovery_island" not in required:
        return {
            "status": "FAILED",
            "summary": "task is not scoped to the recovery island",
            "result": {"reason": "recovery_island_capability_required"},
        }
    runbook = payload.get("recovery_island_runbook")
    if not isinstance(runbook, dict):
        return {
            "status": "FAILED",
            "summary": "signed recovery-island runbook missing",
            "result": {"reason": "missing_recovery_island_runbook"},
        }
    result = executor.execute(runbook)
    return {
        "status": "DONE" if result.get("ok") else "FAILED",
        "summary": f"recovery island {result.get('status')}",
        "result": result,
    }


def run_loop(args: argparse.Namespace) -> int:
    assistx_url = args.assistx_url.rstrip("/")
    auth = (args.auth_user, args.auth_pass)
    headers = {"X-Fleet-Node-Token": args.node_token} if args.node_token else {}
    executor = _executor(args)
    stop = threading.Event()

    def heartbeat(task_id: str, claim_id: str, finished: threading.Event) -> None:
        while not finished.wait(max(5, args.heartbeat_interval)):
            status, _ = _http(
                "POST",
                f"{assistx_url}/api/tasks/{task_id}/heartbeat",
                auth=auth,
                headers=headers,
                data={
                    "agent_id": args.node_id,
                    "claim_id": claim_id,
                    "status": "RUNNING",
                    "lease_seconds": args.lease_seconds,
                },
                timeout=20,
            )
            if status in {404, 409}:
                return

    print(
        f"[recovery-island] node={args.node_id} capability=recovery_island",
        flush=True,
    )
    while not stop.is_set():
        query = urllib.parse.urlencode(
            [
                ("status", "READY"),
                ("limit", "1"),
                ("agent_id", args.node_id),
                ("capabilities", "recovery_island"),
            ]
        )
        status, response = _http(
            "GET",
            f"{assistx_url}/api/agent/tasks?{query}",
            auth=auth,
            headers=headers,
            timeout=20,
        )
        items = response.get("items", []) if isinstance(response, dict) else []
        if status == 200 and items:
            sparse = items[0]
            task_id = str(sparse.get("id") or sparse.get("task_id") or "")
            if not task_id:
                time.sleep(args.poll_interval)
                continue
            detail_status, detail = _http(
                "GET",
                f"{assistx_url}/api/tasks/{task_id}",
                auth=auth,
                headers=headers,
                timeout=20,
            )
            task = (
                detail.get("task")
                if detail_status == 200 and isinstance(detail, dict)
                else sparse
            )
            if not isinstance(task, dict):
                time.sleep(args.poll_interval)
                continue
            claim_status, claimed = _http(
                "POST",
                f"{assistx_url}/api/tasks/{task_id}/claim",
                auth=auth,
                headers=headers,
                data={
                    "agent_id": args.node_id,
                    "capabilities": ["recovery_island"],
                    "lease_seconds": args.lease_seconds,
                },
                timeout=20,
            )
            claimed_task = (
                claimed.get("task") if isinstance(claimed, dict) else None
            )
            if claim_status != 200 or not isinstance(claimed_task, dict):
                time.sleep(args.poll_interval)
                continue
            task.update(claimed_task)
            claim_id = str(claimed_task.get("claim_id") or "")
            finished = threading.Event()
            heart = threading.Thread(
                target=heartbeat,
                args=(task_id, claim_id, finished),
                daemon=True,
                name=f"recovery-island-heartbeat:{task_id}",
            )
            heart.start()
            outcome = execute_task(task, executor)
            finished.set()
            heart.join(timeout=2)
            _http(
                "POST",
                f"{assistx_url}/api/tasks/{task_id}/complete",
                auth=auth,
                headers=headers,
                data={
                    "agent_id": args.node_id,
                    "claim_id": claim_id,
                    "status": outcome["status"],
                    "summary": outcome["summary"],
                    "result": outcome["result"],
                },
                timeout=20,
            )
            print(
                f"[recovery-island] {task_id} -> {outcome['status']}",
                flush=True,
            )
        elif status not in {0, 200}:
            print(
                f"[recovery-island] poll failed status={status} body={str(response)[:160]}",
                flush=True,
            )
        stop.wait(args.poll_interval)
    return 0


def _load_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dedicated AssistX recovery-island executor",
    )
    parser.add_argument(
        "--node-id",
        default=os.getenv("FLEET_NODE_ID")
        or f"{platform.node()}-{platform.machine()}",
    )
    parser.add_argument(
        "--state-dir",
        default=os.getenv(
            "FLEET_RECOVERY_ISLAND_STATE_DIR",
            "/var/lib/assistx-recovery/state",
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    loop = subparsers.add_parser("loop")
    loop.add_argument(
        "--assistx-url",
        default=os.getenv("FLEET_ASSISTX_URL", "http://assistx:8000"),
    )
    loop.add_argument(
        "--auth-user",
        default=os.getenv("FLEET_AUTH_USER", "admin"),
    )
    loop.add_argument(
        "--auth-pass",
        default=os.getenv("FLEET_AUTH_PASS", "change-me"),
    )
    loop.add_argument(
        "--node-token",
        default=os.getenv("FLEET_NODE_TOKEN", ""),
    )
    loop.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.getenv("FLEET_POLL_INTERVAL", "15")),
    )
    loop.add_argument(
        "--heartbeat-interval",
        type=int,
        default=int(os.getenv("FLEET_TASK_HEARTBEAT_SECONDS", "15")),
    )
    loop.add_argument(
        "--lease-seconds",
        type=int,
        default=int(os.getenv("FLEET_TASK_LEASE_SECONDS", "900")),
    )

    status = subparsers.add_parser("status")
    status.add_argument("deployment")

    execute_file = subparsers.add_parser("execute-file")
    execute_file.add_argument("path")

    activate_file = subparsers.add_parser("activate-file")
    activate_file.add_argument("deployment")
    activate_file.add_argument("path")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    executor = _executor(args)
    if args.command == "loop":
        raise SystemExit(run_loop(args))
    if args.command == "status":
        print(json.dumps(executor.status(args.deployment), indent=2, sort_keys=True))
        return
    if args.command == "execute-file":
        result = executor.execute(_load_json(args.path))
    else:
        result = executor.activate_from_envelope(
            args.deployment,
            _load_json(args.path),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
