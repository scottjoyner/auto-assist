"""Central fleet executor — the "helper" that does the work so agents don't have to.

Agents only need an LM Studio endpoint. The executor:
1. Discovers nodes + their model inventory from the router, probing each for
   live LM Studio endpoints and the models they have loaded.
2. Polls AssistX for READY tasks.
3. For each task, finds the **best** capable node:
   - ``llm`` tasks with a specific model → node that has it loaded.
   - Generic ``llm`` → round-robin across all LM Studio nodes.
   - ``script`` → subprocess on the executor host.
4. Handles the full lifecycle: claim → execute → complete (idempotent).
5. Tracks node health: skips unresponsive nodes, logs failures.

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
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

EXECUTOR_INTERVAL = float(os.getenv("FLEET_EXECUTOR_INTERVAL", "15"))
LMSTUDIO_PORT = int(os.getenv("FLEET_LMSTUDIO_PORT", "1234"))
MAX_CONCURRENT = int(os.getenv("FLEET_EXECUTOR_CONCURRENCY", "4"))
NODE_HEALTH_TTL = float(os.getenv("FLEET_NODE_HEALTH_TTL", "120"))
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
        self._rr_index: int = 0

    def _refresh_nodes(self) -> None:
        st, body = _http("GET", f"{ROUTER_URL}/api/fleet/nodes", timeout=10)
        if st != 200:
            logger.warning("fleet executor: router nodes fetch failed (%s)", st)
            return
        nodes = body.get("nodes") if isinstance(body, dict) else body
        if not isinstance(nodes, list):
            return
        enriched = []
        now = time.time()
        for n in nodes:
            ip = n.get("ip")
            if not ip:
                continue
            caps = set(n.get("capabilities") or [])
            caps.add("linux")
            models = self._probe_models(ip)
            if models is not None:
                caps.add("llm")
                n["loaded_models"] = models
                n["lmstudio_ok"] = True
            else:
                n["loaded_models"] = []
                n["lmstudio_ok"] = False
            if self._probe_script():
                caps.add("script")
            n["capabilities"] = list(caps)
            n["last_seen"] = now
            enriched.append(n)

        with self._node_lock:
            self._nodes = enriched
        llm_count = sum(1 for n in enriched if n.get("lmstudio_ok"))
        total_models = sum(len(n.get("loaded_models", [])) for n in enriched if n.get("lmstudio_ok"))
        if llm_count:
            logger.info(
                "fleet executor: %d nodes with LM Studio (%d models): %s",
                llm_count, total_models,
                {n["hostname"]: len(n.get("loaded_models", [])) for n in enriched if n.get("lmstudio_ok")},
            )

    @staticmethod
    def _probe_models(ip: str) -> Optional[list[str]]:
        """Probe LM Studio for model list. Returns list of model IDs or None
        if unreachable."""
        try:
            req = urllib.request.Request(
                f"http://{ip}:{LMSTUDIO_PORT}/v1/models",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read().decode())
                models = data.get("data") or []
                return [m["id"] for m in models if isinstance(m, dict) and m.get("id")]
        except Exception:
            return None

    @staticmethod
    def _probe_script() -> bool:
        try:
            subprocess.run("true", shell=True, capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def _pick_node(self, required: list[str], preferred_model: str = "", exclude: set[str] | None = None) -> Optional[dict]:
        """Pick the best node for a task. If a specific model is requested,
        prefer nodes that have it loaded. Otherwise round-robin across
        capable nodes. Skips nodes that haven't been seen recently.
        ``exclude`` is a set of hostnames/IPs to skip."""
        with self._node_lock:
            candidates = list(self._nodes)
        now = time.time()
        exclude = exclude or set()
        alive = [
            n for n in candidates
            if (now - n.get("last_seen", 0)) < NODE_HEALTH_TTL
            and n.get("hostname", n.get("ip", "")) not in exclude
        ]
        matched = []
        for n in alive:
            caps = set(n.get("capabilities") or [])
            if caps.issuperset(required):
                matched.append(n)

        if not matched:
            return None

        if preferred_model and "llm" in required:
            model_lower = preferred_model.lower()
            for n in matched:
                node_models = [m.lower() for m in n.get("loaded_models", [])]
                if any(preferred_model in m or m in model_lower for m in node_models):
                    return n
            for n in matched:
                node_models_lower = [m.lower() for m in n.get("loaded_models", [])]
                if any(model_lower in m for m in node_models_lower):
                    return n

        self._rr_index = (self._rr_index + 1) % len(matched)
        return matched[self._rr_index]

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
        hostname = node.get("hostname", ip)
        url = f"http://{ip}:{LMSTUDIO_PORT}/v1/chat/completions"
        payload = {
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        if model:
            payload["model"] = model
        elif node.get("loaded_models"):
            payload["model"] = node["loaded_models"][0]

        logger.info(
            "fleet executor: calling LM Studio on %s model=%s",
            hostname, payload.get("model", "default"),
        )

        st, body = _http("POST", url, data=payload, timeout=timeout)

        # If the model isn't actually loaded (LM Studio library entry but
        # not in GPU), fall back to the default model / no model specified.
        if st == 400 and isinstance(body, dict):
            err_msg = (
                body.get("error", {})
                .get("message", "")
                if isinstance(body.get("error"), dict)
                else str(body.get("error", ""))
            )
            if "Failed to load model" in err_msg and payload.get("model"):
                logger.warning(
                    "fleet executor: model '%s' not loaded on %s, retrying with default",
                    payload["model"], hostname,
                )
                del payload["model"]
                st, body = _http("POST", url, data=payload, timeout=timeout)

        if st != 200:
            logger.warning("fleet executor: LM Studio %s returned %s", hostname, st)
            return {"error": body, "status_code": st, "exit_code": 1}

        choice = body.get("choices", [{}])[0]
        return {
            "content": choice.get("message", {}).get("content", ""),
            "model": body.get("model", payload.get("model", "")),
            "usage": body.get("usage", {}),
            "node": hostname,
            "exit_code": 0,
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
            tried: set[str] = set()
            for _ in range(min(4, len(self._nodes) + 1)):
                node = self._pick_node(["llm"], preferred_model=model, exclude=tried)
                if not node:
                    break
                hn = node.get("hostname", node.get("ip", "?"))
                tried.add(hn)
                result = self._call_lmstudio(node, messages, model)
                result["node"] = hn
                if result.get("exit_code", 1) == 0:
                    return result
                logger.warning(
                    "fleet executor: node %s failed, trying next",
                    hn,
                )
            return {"error": "all llm nodes failed", "exit_code": 1}

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
            "fleet executor: %s (%s) caps=%s",
            task_id, row.get("title", "?"), req_caps,
        )

        ik = f"fleet-exec/{task_id}"

        st, claim = _http(
            "POST",
            f"{ASSISTX_URL}/api/tasks/{task_id}/claim",
            data={"agent_id": "fleet-executor", "idempotency_key": ik},
            timeout=15,
        )
        if st != 200:
            logger.info("fleet executor: claim %s failed (%s)", task_id, st)
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
            logger.info("fleet executor: %s -> DONE  node=%s", task_id, result.get("node", "local"))
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
