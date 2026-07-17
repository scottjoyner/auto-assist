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
        --auth-user admin --auth-pass fuck-you \
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
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_CAPS = ["script"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http(
    method: str,
    url: str,
    auth: Optional[tuple[str, str]] = None,
    data: Optional[dict] = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, method=method)
    if auth:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/json")
    if data is not None:
        req.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # network down, DNS, timeout
        return 0, {"error": str(e)}


def detect_capabilities(lmstudio_url: Optional[str]) -> list[str]:
    """Auto-detect what this node can do."""
    caps = list(DEFAULT_CAPS)
    caps.append(platform.system().lower())  # linux / darwin

    # LM Studio / local OpenAI-compatible endpoint?
    if lmstudio_url:
        st, _ = _http("GET", f"{lmstudio_url}/v1/models", timeout=5)
        if st == 200:
            caps.append("llm")
            caps.append("lmstudio")

    # yolo / vision tooling present?
    for exe in ("yolo", "python3"):
        try:
            out = subprocess.run(
                [exe, "-c", "import ultralytics; print('ok')"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and "ok" in out.stdout:
                caps.append("yolo")
                caps.append("vision")
                break
        except Exception:
            continue

    # ffmpeg for media jobs?
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        caps.append("media")
    except Exception:
        pass

    return sorted(set(caps))


def report_to_router(router_url: str, node_id: str, caps: list[str], lmstudio_url: Optional[str]) -> None:
    if not router_url:
        return
    specs = {
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    body = {
        "hostname": node_id,
        "ip": None,
        "capabilities": caps,
        "library": [lmstudio_url] if lmstudio_url else [],
        "loaded": [],
        "specs": specs,
        "health": {"status": "healthy", "reported_at": _now()},
        "os": platform.system(),
    }
    _http("POST", f"{router_url}/api/fleet/node-report", data=body, timeout=10)


def execute_task(task: dict[str, Any], lmstudio_url: Optional[str], workdir: str) -> dict[str, Any]:
    """Run a single task locally. Returns {status, summary, result}."""
    task_id = task.get("id") or task.get("task_id")
    payload = task.get("payload") or {}
    if not payload and task.get("payload_json"):
        try:
            payload = json.loads(task["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
    required = task.get("required_capabilities") or []

    # Prefer an explicit command in the payload (script/agent jobs).
    command = payload.get("command") or payload.get("cmd") or task.get("command")
    if command:
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=workdir, timeout=1800,
            )
            ok = proc.returncode == 0
            return {
                "status": "DONE" if ok else "FAILED",
                "summary": f"exit={proc.returncode}",
                "result": {"stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]},
            }
        except subprocess.TimeoutExpired:
            return {"status": "FAILED", "summary": "timeout", "result": {}}

    # LLM job: call local LM Studio chat completion.
    if "llm" in required and lmstudio_url:
        prompt = payload.get("prompt") or task.get("title") or ""
        st, body = _http(
            "POST", f"{lmstudio_url}/v1/chat/completions",
            data={"model": payload.get("model", "local/model"), "messages": [{"role": "user", "content": prompt}], "max_tokens": 1024},
            timeout=300,
        )
        if st == 200 and isinstance(body, dict):
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"status": "DONE", "summary": "llm response", "result": {"answer": text}}
        return {"status": "FAILED", "summary": f"lm call {st}", "result": body}

    # yolo/vision job: run a detection/inference command if provided.
    if ("yolo" in required or "vision" in required) and payload.get("yolo_command"):
        try:
            proc = subprocess.run(payload["yolo_command"], shell=True, capture_output=True, text=True, cwd=workdir, timeout=1800)
            return {"status": "DONE" if proc.returncode == 0 else "FAILED",
                    "summary": f"yolo exit={proc.returncode}",
                    "result": {"stdout": proc.stdout[-4000:]}}
        except Exception as e:
            return {"status": "FAILED", "summary": str(e), "result": {}}

    # Fallback: nothing actionable — mark FAILED with a clear reason.
    return {
        "status": "FAILED",
        "summary": "no executable handler for task",
        "result": {"required_capabilities": required, "payload_keys": list(payload.keys())},
    }


def run_node(args: argparse.Namespace) -> None:
    auth = (args.auth_user, args.auth_pass)
    node_id = args.node_id or f"{platform.node()}-{platform.machine()}"
    lmstudio_url = args.lmstudio_url or os.getenv("FLEET_LMSTUDIO_URL")
    extra = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]
    caps = detect_capabilities(lmstudio_url) + extra
    caps = sorted(set(caps))

    print(f"[fleet-agent] node={node_id} caps={caps}", flush=True)
    if lmstudio_url:
        print(f"[fleet-agent] lmstudio={lmstudio_url}", flush=True)

    report_to_router(args.router_url, node_id, caps, lmstudio_url)
    sem = threading.Semaphore(max(1, args.concurrency))
    stop = threading.Event()

    def worker_loop() -> None:
        while not stop.is_set():
            try:
                query = urllib.parse.urlencode(
                    [("status", "READY"), ("limit", str(args.concurrency))]
                    + [("capabilities", c) for c in caps]
                )
                st, resp = _http(
                    "GET",
                    f"{args.assistx_url}/api/agent/tasks?{query}",
                    auth=auth, timeout=20,
                )
                items = resp.get("items", []) if isinstance(resp, dict) else []
                if st == 200 and items:
                    for task in items:
                        if stop.is_set():
                            break
                        sem.acquire()
                        threading.Thread(target=handle_one, args=(task,), daemon=True).start()
                else:
                    if st not in (200, 0):
                        print(f"[fleet-agent] poll {st}: {str(resp)[:120]}", flush=True)
            except Exception as e:
                print(f"[fleet-agent] loop err: {e}", flush=True)
            # heartbeat re-report occasionally
            report_to_router(args.router_url, node_id, caps, lmstudio_url)
            time.sleep(args.poll_interval)

    def handle_one(task: dict[str, Any]) -> None:
        try:
            task_id = task.get("id") or task.get("task_id")
            # The list endpoint returns sparse items; fetch full detail (incl.
            # payload_json) before executing.
            st_get, full = _http(
                "GET", f"{args.assistx_url}/api/tasks/{task_id}", auth=auth, timeout=20,
            )
            if st_get == 200 and isinstance(full, dict) and full.get("task"):
                task = full["task"]
            print(f"[fleet-agent] claim {task_id}", flush=True)
            st, resp = _http(
                "POST", f"{args.assistx_url}/api/tasks/{task_id}/claim",
                auth=auth, data={"agent_id": node_id, "capabilities": caps, "lease_seconds": 1800},
                timeout=20,
            )
            if st != 200 or not (isinstance(resp, dict) and resp.get("claimed")):
                print(f"[fleet-agent] claim {task_id} rejected: {st}", flush=True)
                return
            outcome = execute_task(task, lmstudio_url, args.workdir)
            _http(
                "POST", f"{args.assistx_url}/api/tasks/{task_id}/complete",
                auth=auth, data={"agent_id": node_id, "status": outcome["status"],
                                 "summary": outcome.get("summary"), "result": outcome.get("result")},
                timeout=20,
            )
            print(f"[fleet-agent] {task_id} -> {outcome['status']}", flush=True)
        except Exception as e:
            print(f"[fleet-agent] handle {task.get('id')} err: {e}", flush=True)
        finally:
            sem.release()

    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        print("[fleet-agent] shutting down", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Fleet node-agent")
    p.add_argument("--assistx-url", default=os.getenv("FLEET_ASSISTX_URL", "http://assistx:8000"))
    p.add_argument("--router-url", default=os.getenv("FLEET_ROUTER_URL", "http://router:8088"))
    p.add_argument("--auth-user", default=os.getenv("FLEET_AUTH_USER", "admin"))
    p.add_argument("--auth-pass", default=os.getenv("FLEET_AUTH_PASS", "fuck-you"))
    p.add_argument("--node-id", default=os.getenv("FLEET_NODE_ID"))
    p.add_argument("--lmstudio-url", default=os.getenv("FLEET_LMSTUDIO_URL"))
    p.add_argument("--capabilities", default=os.getenv("FLEET_CAPABILITIES", ""))
    p.add_argument("--poll-interval", type=int, default=int(os.getenv("FLEET_POLL_INTERVAL", "10")))
    p.add_argument("--concurrency", type=int, default=int(os.getenv("FLEET_CONCURRENCY", "2")))
    p.add_argument("--workdir", default=os.getenv("FLEET_WORKDIR", "/tmp/fleet-work"))
    args = p.parse_args()
    os.makedirs(args.workdir, exist_ok=True)
    run_node(args)


if __name__ == "__main__":
    main()
