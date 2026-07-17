"""Central fleet executor — the "helper" that does the work so agents don't have to.

Agents only need an LM Studio endpoint. The executor:
1. Discovers nodes + their capabilities from the router.
2. Polls Neo4j for READY tasks.
3. For each task, finds a capable node and executes directly:
   - ``script`` → subprocess on the executor host (or SSH).
   - ``llm`` → calls LM Studio ``/v1/chat/completions`` on the node.
4. Handles the full lifecycle: claim → execute → complete.

No agent-side code beyond running LM Studio.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

EXECUTOR_INTERVAL = float(os.getenv("FLEET_EXECUTOR_INTERVAL", "15"))
LMSTUDIO_PORT = int(os.getenv("FLEET_LMSTUDIO_PORT", "1234"))
MAX_CONCURRENT = int(os.getenv("FLEET_EXECUTOR_CONCURRENCY", "4"))
BASIC_AUTH_USER = os.getenv("FLEET_BASIC_AUTH_USER", "admin")
BASIC_AUTH_PASS = os.getenv("FLEET_BASIC_AUTH_PASS", "fuck-you")
ASSISTX_URL = os.getenv("FLEET_ASSISTX_URL", "http://assistx:8000")
ROUTER_URL = os.getenv("FLEET_ROUTER_URL", "http://router:8088")


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _http(
    method: str,
    url: str,
    data: Optional[dict] = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    headers = {}
    if BASIC_AUTH_USER and BASIC_AUTH_PASS:
        import base64
        raw = f"{BASIC_AUTH_USER}:{BASIC_AUTH_PASS}"
        headers["Authorization"] = f"Basic {base64.b64encode(raw.encode()).decode()}"
    body = json.dumps(data).encode() if data else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = {"error": str(e)}
        return e.code, detail
    except Exception as e:
        return 0, {"error": str(e)}


class FleetExecutor:
    """Central task executor — claims READY tasks and runs them against fleet
    nodes by capability. Runs as a daemon thread inside the assistx process."""

    def __init__(self) -> None:
        self._nodes: list[dict] = []
        self._node_lock = threading.Lock()
        self._semaphore = threading.Semaphore(MAX_CONCURRENT)

    def _refresh_nodes(self) -> None:
        st, body = _http("GET", f"{ROUTER_URL}/api/fleet/nodes", timeout=10)
        if st != 200:
            logger.warning("fleet executor: router nodes fetch failed (%s)", st)
            return
        nodes = body.get("nodes") if isinstance(body, dict) else body
        if not isinstance(nodes, list):
            return
        enriched = []
        for n in nodes:
            ip = n.get("ip")
            if not ip:
                continue
            caps = set(n.get("capabilities") or [])
            caps.add("linux")
            if self._probe_lmstudio(ip):
                caps.add("llm")
            if self._probe_script():
                caps.add("script")
            n["capabilities"] = list(caps)
            enriched.append(n)
        with self._node_lock:
            self._nodes = enriched
        logger.debug(
            "fleet executor: refreshed %d nodes (%d with llm)",
            len(enriched),
            sum(1 for n in enriched if "llm" in n.get("capabilities", [])),
        )

    @staticmethod
    def _probe_lmstudio(ip: str) -> bool:
        try:
            req = urllib.request.Request(
                f"http://{ip}:{LMSTUDIO_PORT}/v1/models",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    @staticmethod
    def _probe_script() -> bool:
        try:
            subprocess.run("true", shell=True, capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def _capable_nodes(self, required: list[str]) -> list[dict]:
        with self._node_lock:
            candidates = list(self._nodes)
        matched = []
        for n in candidates:
            caps = set(n.get("capabilities") or [])
            if caps.issuperset(required):
                matched.append(n)
        return matched

    def _run_script(self, command: str, timeout: int = 120) -> dict:
        try:
            r = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return {"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "timeout", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def _call_lmstudio(self, node: dict, messages: list[dict], model: str = "", timeout: int = 180) -> dict:
        ip = node.get("ip", "127.0.0.1")
        url = f"http://{ip}:{LMSTUDIO_PORT}/v1/chat/completions"
        payload = {
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        if model:
            payload["model"] = model
        elif node.get("loaded"):
            payload["model"] = node["loaded"][0]
        logger.info("fleet executor: calling LM Studio on %s:%s", ip, LMSTUDIO_PORT)
        st, body = _http("POST", url, data=payload, timeout=timeout)
        if st != 200:
            return {"error": body, "status_code": st}
        choice = body.get("choices", [{}])[0]
        return {
            "content": choice.get("message", {}).get("content", ""),
            "model": body.get("model", model),
            "usage": body.get("usage", {}),
        }

    def _execute_task(self, task: dict) -> dict:
        payload = task.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        req_caps = task.get("required_capabilities") or task.get("required_capabilities_plain") or ["script"]

        if "llm" in req_caps:
            messages = payload.get("messages") or [
                {"role": "user", "content": payload.get("prompt", payload.get("command", ""))}
            ]
            model = payload.get("model", "")
            nodes = self._capable_nodes(["llm"])
            if not nodes:
                return {"error": "no node with llm capability available", "exit_code": 1}
            result = self._call_lmstudio(nodes[0], messages, model)
            return result

        if "script" in req_caps:
            command = payload.get("command") or payload.get("prompt", "")
            result = self._run_script(command)
            return result

        return {"error": f"unhandled capabilities: {req_caps}", "exit_code": 1}

    def _process_tasks(self) -> None:
        self._refresh_nodes()
        st, body = _http(
            "GET",
            f"{ASSISTX_URL}/api/agent/tasks?status=READY&limit={MAX_CONCURRENT}",
            timeout=15,
        )
        if st != 200:
            logger.warning("fleet executor: task fetch failed (%s)", st)
            return
        rows = body.get("items") if isinstance(body, dict) else body
        if not isinstance(rows, list):
            return

        if not rows:
            return

        for row in rows:
            self._semaphore.acquire()
            t = threading.Thread(
                target=self._handle_one,
                args=(row,),
                daemon=True,
            )
            t.start()

    def _handle_one(self, row: dict) -> None:
        try:
            self._do_handle(row)
        finally:
            self._semaphore.release()

    def _do_handle(self, row: dict) -> None:
        task_id = row["id"]
        payload_raw = row.get("payload_json") or row.get("payload") or "{}"
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            payload = {}
        raw_caps = row.get("required_capabilities") or []
        if isinstance(raw_caps, str):
            try:
                req_caps = json.loads(raw_caps)
            except Exception:
                req_caps = ["script"]
        else:
            req_caps = list(raw_caps) if raw_caps else ["script"]

        logger.info(
            "fleet executor: processing %s (%s) caps=%s",
            task_id, row.get("title", "?"), req_caps,
        )

        # Deterministic idempotency key so retried claims are safe even if
        # Neo4j committed on the server but the TCP response was lost (known
        # Docker-bridge + Neo4j driver behaviour).
        ik = f"fleet-exec/{task_id}"

        st, claim = _http(
            "POST",
            f"{ASSISTX_URL}/api/tasks/{task_id}/claim",
            data={"agent_id": "fleet-executor", "idempotency_key": ik},
            timeout=15,
        )
        if st != 200:
            logger.info("fleet executor: claim %s failed (%s) — skipping", task_id, st)
            return
        if not claim.get("claimed", False):
            return

        task_dict = {"id": task_id, "payload": payload, "required_capabilities": req_caps}
        result = self._execute_task(task_dict)

        st, _ = _http(
            "POST",
            f"{ASSISTX_URL}/api/tasks/{task_id}/complete",
            data={
                "agent_id": "fleet-executor",
                "status": "DONE" if result.get("exit_code", 0) == 0 else "FAILED",
                "result": result,
                "idempotency_key": f"fleet-exec/complete/{task_id}",
            },
            timeout=15,
        )
        if st == 200:
            logger.info("fleet executor: %s -> DONE", task_id)
        else:
            logger.warning("fleet executor: %s complete failed (%s)", task_id, st)

    def run_once(self) -> None:
        """One poll + process cycle. For testing or manual trigger."""
        self._refresh_nodes()
        self._process_tasks()


def _start_executor_loop() -> None:
    """Start the daemon background thread."""

    def _loop() -> None:
        executor = FleetExecutor()
        time.sleep(15)
        logger.info("fleet executor: starting poll loop (every %ss)", EXECUTOR_INTERVAL)
        while True:
            try:
                executor._refresh_nodes()
                executor._process_tasks()
            except Exception as e:
                logger.warning("fleet executor: cycle error: %s", e)
            time.sleep(EXECUTOR_INTERVAL)

    t = threading.Thread(target=_loop, name="fleet-executor", daemon=True)
    t.start()
    logger.info("fleet executor: daemon thread started")
