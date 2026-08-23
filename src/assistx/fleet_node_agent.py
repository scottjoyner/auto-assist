"""Fleet node-agent: turns any idle machine into a fleet worker.

Design goals (per fleet unification plan):
- **Zero pip dependencies** — stdlib only, so it runs on weak Linux boxes and
  Apple-Silicon Macs that struggle to even load a 1.2b model.
- **Capability-aware** — auto-detects what the node can do (local LM Studio
  endpoint, yolo/vision, shell-script execution) and only pulls tasks whose
  ``required_capabilities`` it satisfies. Weak nodes run ``script`` jobs;
  Macs with vision tooling run ``yolo``/``vision`` jobs; anything with an LM
  Studio endpoint runs ``llm`` jobs.
- **Self-joining** — reports its capabilities + health to the auto-router
  ``/api/fleet/node-report`` endpoint so the router's context model stays
  current, and polls AssistX ``/api/agent/tasks`` for work.
- **Crash-safe** — claims with a lease, heartbeats, and reports DONE/FAILED.
  A node that dies mid-task leaves the lease to expire so another node can
  retry.

Run:
    python -m assistx.fleet_node_agent \
        --assistx-url http://assistx:8000 \
        --router-url http://router:8088 \
        --auth-user admin --auth-pass change-me \
        --poll-interval 10 --concurrency 2

Env equivalents: FLEET_ASSISTX_URL, FLEET_ROUTER_URL, FLEET_AUTH_USER,
FLEET_AUTH_PASS, FLEET_POLL_INTERVAL, FLEET_CONCURRENCY, FLEET_LMSTUDIO_URL,
FLEET_CAPABILITIES (comma-separated extra caps), FLEET_NODE_ID (override).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .kv_cache import runtime_capabilities
from .recovery_runbooks import RecoveryRunbookExecutor

DEFAULT_CAPS = ["script"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _http(
    method: str,
    url: str,
    auth: tuple[str, str] | None = None,
    data: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, method=method)
    if auth:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if data is not None:
        req.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {"detail": str(exc)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"detail": str(exc)}


def _detect_hostname() -> str:
    return os.getenv("FLEET_NODE_ID") or platform.node()


def _detect_capabilities(lmstudio_url: str | None) -> tuple[list[str], list[str]]:
    caps = set(DEFAULT_CAPS)
    models: list[str] = []

    if lmstudio_url:
        status, body = _http("GET", f"{lmstudio_url.rstrip('/')}/v1/models", timeout=5)
        if status == 200 and isinstance(body, dict):
            caps.add("llm")
            models = [item.get("id", "") for item in body.get("data", []) if item.get("id")]

    # Lightweight vision/tooling detection without importing heavyweight libs.
    for command in ("ffmpeg", "python3"):
        if subprocess.run(
            ["which", command], capture_output=True, text=True, check=False
        ).returncode == 0:
            caps.add(command)

    extra = os.getenv("FLEET_CAPABILITIES", "")
    caps.update(cap.strip() for cap in extra.split(",") if cap.strip())
    return sorted(caps), models


def _health_payload(node_id: str, caps: list[str], models: list[str]) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "capabilities": caps,
        "models": models,
        "healthy": True,
        "reported_at": _now(),
    }


def _auth_from_env() -> tuple[str, str] | None:
    user = os.getenv("FLEET_AUTH_USER", "")
    password = os.getenv("FLEET_AUTH_PASS", "")
    if user or password:
        return user, password
    return None


def execute_task(
    task: dict[str, Any],
    lmstudio_url: str | None,
    workdir: str,
    *,
    node_id: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute a deterministic benchmark task against the local LM Studio.

    Restored public contract (dropped in 9d8751a5's executor hardening):
    benchmark_controller creates ``payload.benchmark`` tasks and expects
    quality-scored outcomes ({quality_score, validation_passed,
    tokens_per_second}). ``workdir`` is accepted for signature compatibility;
    deterministic scoring never touches the filesystem.
    """
    payload = task.get("payload") or {}
    if not payload and task.get("payload_json"):
        try:
            payload = json.loads(task["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
    if not payload.get("benchmark"):
        return {"status": "FAILED", "summary": "not a benchmark task", "result": {}}

    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    should_stop = should_stop or (lambda: False)
    scores: list[float] = []
    token_total = 0
    elapsed_total = 0.0
    case_results: list[dict[str, Any]] = []
    for index, case in enumerate(cases[:10]):
        if should_stop():
            progress = index / max(min(len(cases), 10), 1)
            return {
                "status": "PAUSED",
                "summary": f"benchmark paused at case {index}",
                "progress": progress,
                "checkpoint": {
                    "handler": "benchmark",
                    "next_case_index": index,
                    "scores": scores,
                    "token_total": token_total,
                    "elapsed_total": elapsed_total,
                    "case_results": case_results,
                },
            }
        started = time.perf_counter()
        st, body = _http(
            "POST",
            f"{(lmstudio_url or '').rstrip('/')}/v1/chat/completions",
            data={
                "model": payload.get("model"),
                "messages": [{"role": "user", "content": str(case.get("prompt") or "")}],
                "temperature": 0,
                "max_tokens": int(payload.get("max_tokens_per_case") or 256),
            },
            timeout=min(300, int(payload.get("deadline_seconds") or 900)),
        )
        elapsed = max(time.perf_counter() - started, 0.001)
        text = ""
        if st == 200 and isinstance(body, dict):
            choices = body.get("choices") or [{}]
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            text = str((message or {}).get("content", ""))
        terms = [str(term).lower() for term in case.get("required_terms") or []]
        term_score = sum(term in text.lower() for term in terms) / max(len(terms), 1)
        length_ok = len(text.strip()) >= int(case.get("min_chars") or 1)
        score = term_score * (1.0 if length_ok else 0.5) if st == 200 else 0.0
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        tokens = int(usage.get("completion_tokens") or max(1, len(text) // 4))
        token_total += tokens
        elapsed_total += elapsed
        scores.append(score)
        case_results.append({"ok": score >= 0.7, "score": round(score, 3), "latency_ms": int(elapsed * 1000)})
    quality = sum(scores) / len(scores) if scores else 0.0
    return {
        "status": "DONE" if cases and quality >= 0.5 else "FAILED",
        "summary": f"benchmark quality={quality:.3f}",
        "result": {
            "quality_score": round(quality, 3),
            "validation_passed": bool(cases and quality >= 0.7),
            "tokens_per_second": round(token_total / max(elapsed_total, 0.001), 3),
            "case_count": len(cases),
            "cases": case_results,
            "model": payload.get("model"),
            "task_family": payload.get("task_family"),
        },
    }


def _claim_and_run(
    *,
    assistx_url: str,
    router_url: str,
    auth: tuple[str, str] | None,
    node_id: str,
    caps: list[str],
    task: dict[str, Any],
    lmstudio_url: str | None,
) -> None:
    task_id = str(task.get("task_id") or task.get("id") or "")
    if not task_id:
        return

    status, claim = _http(
        "POST",
        f"{assistx_url.rstrip('/')}/api/agent/tasks/{task_id}/claim",
        auth=auth,
        data={"node_id": node_id},
    )
    if status >= 300 or not isinstance(claim, dict):
        return
    claim_id = claim.get("claim_id")

    stop_heartbeat = threading.Event()

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(15):
            _http(
                "POST",
                f"{assistx_url.rstrip('/')}/api/agent/tasks/{task_id}/heartbeat",
                auth=auth,
                data={"node_id": node_id, "claim_id": claim_id},
                timeout=10,
            )

    heartbeat = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat.start()

    result: dict[str, Any]
    success = False
    try:
        task_type = str(task.get("task_type") or "").lower()
        if task_type == "script":
            result = RecoveryRunbookExecutor.execute_task_payload(task)
            success = bool(result.get("success"))
        elif task_type == "llm":
            if not lmstudio_url:
                result = {"error": "LM Studio endpoint unavailable"}
            else:
                raw_payload = task.get("payload") or {}
                if not raw_payload and task.get("payload_json"):
                    try:
                        raw_payload = json.loads(task["payload_json"])
                    except (json.JSONDecodeError, TypeError):
                        raw_payload = {}
                if isinstance(raw_payload, dict) and raw_payload.get("benchmark"):
                    result = execute_task(task, lmstudio_url, workdir=".", node_id=node_id)
                    success = result.get("status") == "DONE"
                else:
                    prompt = str(task.get("prompt") or task.get("description") or "")
                    model = str(task.get("model") or "")
                    request: dict[str, Any] = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    }
                    llm_status, body = _http(
                        "POST",
                        f"{lmstudio_url.rstrip('/')}/v1/chat/completions",
                        data=request,
                        timeout=300,
                    )
                    success = llm_status == 200
                    result = body if isinstance(body, dict) else {"result": body}
        else:
            result = {"error": f"unsupported task_type={task_type}"}
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        result = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=1)

    endpoint = "complete" if success else "fail"
    _http(
        "POST",
        f"{assistx_url.rstrip('/')}/api/agent/tasks/{task_id}/{endpoint}",
        auth=auth,
        data={
            "node_id": node_id,
            "claim_id": claim_id,
            "result": result,
        },
    )


def _poll_once(
    *,
    assistx_url: str,
    router_url: str,
    auth: tuple[str, str] | None,
    node_id: str,
    caps: list[str],
    lmstudio_url: str | None,
) -> bool:
    status, body = _http(
        "GET",
        f"{assistx_url.rstrip('/')}/api/agent/tasks?state=READY&limit=20",
        auth=auth,
        timeout=15,
    )
    if status != 200 or not isinstance(body, dict):
        return False
    tasks = body.get("tasks", body.get("items", []))
    if not isinstance(tasks, list):
        return False

    for task in tasks:
        if not isinstance(task, dict):
            continue
        required = set(task.get("required_capabilities") or [])
        if required.issubset(set(caps)):
            _claim_and_run(
                assistx_url=assistx_url,
                router_url=router_url,
                auth=auth,
                node_id=node_id,
                caps=caps,
                task=task,
                lmstudio_url=lmstudio_url,
            )
            return True
    return False


def run(
    *,
    assistx_url: str,
    router_url: str,
    poll_interval: float,
    concurrency: int,
    lmstudio_url: str | None,
) -> None:
    del concurrency  # current worker loop intentionally executes one claimed task at a time
    node_id = _detect_hostname()
    auth = _auth_from_env()
    caps, models = _detect_capabilities(lmstudio_url)

    while True:
        _http(
            "POST",
            f"{router_url.rstrip('/')}/api/fleet/node-report",
            auth=auth,
            data=_health_payload(node_id, caps, models),
            timeout=10,
        )
        worked = _poll_once(
            assistx_url=assistx_url,
            router_url=router_url,
            auth=auth,
            node_id=node_id,
            caps=caps,
            lmstudio_url=lmstudio_url,
        )
        if not worked:
            time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run this machine as an AssistX fleet worker")
    parser.add_argument(
        "--assistx-url", default=os.getenv("FLEET_ASSISTX_URL", "http://assistx:8000")
    )
    parser.add_argument(
        "--router-url", default=os.getenv("FLEET_ROUTER_URL", "http://router:8088")
    )
    parser.add_argument(
        "--poll-interval", type=float, default=float(os.getenv("FLEET_POLL_INTERVAL", "10"))
    )
    parser.add_argument(
        "--concurrency", type=int, default=int(os.getenv("FLEET_CONCURRENCY", "1"))
    )
    parser.add_argument("--lmstudio-url", default=os.getenv("FLEET_LMSTUDIO_URL"))
    args = parser.parse_args()
    run(
        assistx_url=args.assistx_url,
        router_url=args.router_url,
        poll_interval=args.poll_interval,
        concurrency=args.concurrency,
        lmstudio_url=args.lmstudio_url,
    )


if __name__ == "__main__":
    main()
