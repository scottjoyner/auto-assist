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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # network down, DNS, timeout
        return 0, {"error": str(e)}


def detect_capabilities(lmstudio_url: str | None) -> list[str]:
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


def report_to_router(
    router_url: str,
    node_id: str,
    caps: list[str],
    lmstudio_url: str | None,
    *,
    drained: bool = False,
) -> None:
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
        "health": {
            "status": "drained" if drained else "healthy",
            "reported_at": _now(),
            "control_state": "DRAINED" if drained else "ENABLED",
        },
        "os": platform.system(),
    }
    _http("POST", f"{router_url}/api/fleet/node-report", data=body, timeout=10)


def execute_task(
    task: dict[str, Any],
    lmstudio_url: str | None,
    workdir: str,
    *,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Run a single task locally. Returns {status, summary, result}."""
    payload = task.get("payload") or {}
    if not payload and task.get("payload_json"):
        try:
            payload = json.loads(task["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
    required = task.get("required_capabilities") or []

    # Recovery work never enters the generic command path. It is parsed and
    # executed as a typed, allowlisted runbook targeted to this node.
    if payload.get("runbook") or "recovery" in required:
        if os.getenv("FLEET_RECOVERY_RUNBOOKS_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            return {"status": "FAILED", "summary": "recovery runbooks disabled", "result": {"reason": "recovery_runbooks_disabled"}}
        executor = RecoveryRunbookExecutor(
            node_id=node_id or f"{platform.node()}-{platform.machine()}",
            lmstudio_url=lmstudio_url,
            state_dir=str(Path(workdir) / "recovery-state"),
            http=_http,
        )
        result = executor.execute(payload.get("runbook") or {})
        return {
            "status": "DONE" if result.get("ok") else "FAILED",
            "summary": f"recovery {result.get('status')}",
            "result": result,
        }

    # Prefer an explicit command in the payload (script/agent jobs).
    command = payload.get("command") or payload.get("cmd") or task.get("command")
    if command:
        if os.getenv("FLEET_UNSAFE_SHELL_TASKS_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            return {
                "status": "FAILED",
                "summary": "legacy shell tasks disabled",
                "result": {"reason": "unsafe_shell_tasks_disabled"},
            }
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
        if payload.get("benchmark"):
            cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
            scores: list[float] = []
            token_total = 0
            elapsed_total = 0.0
            case_results = []
            for case in cases[:10]:
                started = time.perf_counter()
                st, body = _http(
                    "POST", f"{lmstudio_url}/v1/chat/completions",
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
                    text = str(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
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
        if os.getenv("FLEET_UNSAFE_SHELL_TASKS_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
            return {
                "status": "FAILED",
                "summary": "legacy vision shell tasks disabled",
                "result": {"reason": "unsafe_shell_tasks_disabled"},
            }
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
    node_headers = {"X-Fleet-Node-Token": args.node_token} if args.node_token else {}
    node_id = args.node_id or f"{platform.node()}-{platform.machine()}"
    lmstudio_url = args.lmstudio_url or os.getenv("FLEET_LMSTUDIO_URL")
    extra = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]
    caps = detect_capabilities(lmstudio_url) + extra
    if os.getenv("FLEET_RECOVERY_RUNBOOKS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        caps.append("recovery")
        aliases = os.getenv("FLEET_RECOVERY_SERVICE_ALIASES", "{}")
        if '"systemd"' in aliases or platform.system().lower() == "linux":
            caps.append("recovery:systemd")
        if '"launchd"' in aliases or platform.system().lower() == "darwin":
            caps.append("recovery:launchd")
        if os.getenv("FLEET_RECOVERY_COMPOSE_PROJECTS", "{}").strip() not in {"", "{}"}:
            caps.append("recovery:docker-compose")
        if lmstudio_url:
            caps.append("recovery:lmstudio")
    caps = sorted(set(caps))

    print(f"[fleet-agent] node={node_id} caps={caps}", flush=True)
    if lmstudio_url:
        print(f"[fleet-agent] lmstudio={lmstudio_url}", flush=True)

    drain_path = Path(args.workdir) / "recovery-state" / "drained.json"
    report_to_router(args.router_url, node_id, caps, lmstudio_url, drained=drain_path.exists())
    sem = threading.Semaphore(max(1, args.concurrency))
    stop = threading.Event()

    def worker_loop() -> None:
        while not stop.is_set():
            try:
                poll_caps = ["recovery"] if drain_path.exists() else caps
                query = urllib.parse.urlencode(
                    [("status", "READY"), ("limit", str(args.concurrency)), ("agent_id", node_id)]
                    + [("capabilities", c) for c in poll_caps]
                )
                st, resp = _http(
                    "GET",
                    f"{args.assistx_url}/api/agent/tasks?{query}",
                    auth=auth, headers=node_headers, timeout=20,
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
            report_to_router(
                args.router_url,
                node_id,
                caps,
                lmstudio_url,
                drained=drain_path.exists(),
            )
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
                headers=node_headers,
                timeout=20,
            )
            if st != 200 or not (isinstance(resp, dict) and resp.get("claimed")):
                print(f"[fleet-agent] claim {task_id} rejected: {st}", flush=True)
                return
            outcome = execute_task(task, lmstudio_url, args.workdir, node_id=node_id)
            _http(
                "POST", f"{args.assistx_url}/api/tasks/{task_id}/complete",
                auth=auth, data={"agent_id": node_id, "status": outcome["status"],
                                 "summary": outcome.get("summary"), "result": outcome.get("result")},
                headers=node_headers,
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
    p.add_argument("--auth-pass", default=os.getenv("FLEET_AUTH_PASS", "change-me"))
    p.add_argument("--node-id", default=os.getenv("FLEET_NODE_ID"))
    p.add_argument("--node-token", default=os.getenv("FLEET_NODE_TOKEN", ""))
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
