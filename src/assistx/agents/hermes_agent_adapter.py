from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

ASSISTX_URL = os.getenv("ASSISTX_URL", "http://localhost:8000")
ASSISTX_USER = os.getenv("ASSISTX_USER", "admin")
ASSISTX_PASS = os.getenv("ASSISTX_PASS", "change-me")
AGENT_ID = os.getenv("HERMES_AGENT_ID", "hermes-local")
AGENT_CAPABILITIES = os.getenv("HERMES_AGENT_CAPABILITIES", "terminal,file,code_execution,web").split(",")
POLL_INTERVAL = int(os.getenv("HERMES_POLL_INTERVAL", "15"))
HERMES_BIN = os.getenv("HERMES_BIN", "hermes")
HERMES_TIMEOUT = int(os.getenv("HERMES_TASK_TIMEOUT", "300"))
MAX_TASKS_PER_LOOP = int(os.getenv("HERMES_MAX_TASKS_PER_LOOP", "3"))


class AssistXClient:
    def __init__(
        self,
        base_url: str = ASSISTX_URL,
        username: str = ASSISTX_USER,
        password: str = ASSISTX_PASS,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)

    def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", 30)
        resp = requests.request(method, url, auth=self.auth, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def poll_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        caps_param = "&".join(f"capabilities={c}" for c in AGENT_CAPABILITIES)
        result = self._request(
            "GET",
            f"/api/agent/tasks?status=READY&agent_id={AGENT_ID}&limit={limit}&{caps_param}",
        )
        return result.get("items", [])

    def claim_task(self, task_id: str, session_id: str) -> bool:
        try:
            self._request(
                "POST",
                f"/api/tasks/{task_id}/claim",
                json={
                    "agent_id": AGENT_ID,
                    "capabilities": AGENT_CAPABILITIES,
                    "session_id": session_id,
                },
            )
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                logger.info("Task %s already claimed by another agent", task_id)
                return False
            raise

    def get_context(self, task_id: str, query: str) -> Dict[str, Any]:
        result = self._request(
            "POST",
            "/api/brain/context",
            json={
                "query": query,
                "task_id": task_id,
                "max_items": 20,
                "include_sources": ["memory", "knowledge", "orchestration"],
            },
        )
        return result.get("context_packet", {})

    def heartbeat(self, task_id: str, session_id: str, status: str = "RUNNING") -> None:
        try:
            self._request(
                "POST",
                f"/api/tasks/{task_id}/heartbeat",
                json={
                    "agent_id": AGENT_ID,
                    "status": status,
                    "session_id": session_id,
                },
            )
        except requests.RequestException as e:
            logger.warning("Heartbeat failed for task %s: %s", task_id, e)

    def complete_task(
        self,
        task_id: str,
        session_id: str,
        status: str,
        summary: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            json={
                "agent_id": AGENT_ID,
                "status": status,
                "summary": summary or "",
                "result": result or {},
                "session_id": session_id,
            },
        )

    def write_memory(
        self,
        kind: str,
        text: str,
        source: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        result = self._request(
            "POST",
            "/api/memory/items",
            json={
                "kind": kind,
                "text": text,
                "source": source,
                "task_id": task_id,
                "session_id": session_id,
            },
        )
        return result.get("memory_item_id", "")

    def register_session(self, session_id: str, hermes_session_id: str) -> None:
        self._request(
            "POST",
            f"/api/sessions/{session_id}",
            json={
                "hermes_session_id": hermes_session_id,
                "platform": "linux",
                "metadata": {"agent": AGENT_ID, "source": "hermes-agent-adapter"},
            },
        )


def run_hermes(prompt: str, timeout: int = HERMES_TIMEOUT) -> Dict[str, Any]:
    cmd = [
        HERMES_BIN,
        "chat",
        "-q", prompt,
        "--quiet",
        "--pass-session-id",
        "--max-turns", "20",
    ]
    logger.info("Running Hermes (timeout=%ds)", timeout)
    start = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "HERMES_ACCEPT_HOOKS": "1"},
        )
    except subprocess.TimeoutExpired:
        logger.warning("Hermes timed out after %ds", timeout)
        return {"success": False, "error": "timeout", "output": "", "session_id": None}
    except FileNotFoundError:
        logger.error("Hermes binary not found at %s", HERMES_BIN)
        return {"success": False, "error": "hermes_binary_not_found", "output": "", "session_id": None}

    elapsed = time.monotonic() - start
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    session_id = None

    for line in stdout.splitlines():
        if "session_id:" in line or "Session ID:" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                session_id = parts[1].strip()
                break

    if proc.returncode != 0:
        logger.warning("Hermes exit code %d (elapsed=%.1fs)", proc.returncode, elapsed)
        return {
            "success": False,
            "error": f"exit_code_{proc.returncode}",
            "output": stdout,
            "stderr": stderr,
            "session_id": session_id,
            "elapsed": elapsed,
        }

    logger.info("Hermes completed in %.1fs (session=%s)", elapsed, session_id or "N/A")
    return {
        "success": True,
        "output": stdout.strip(),
        "session_id": session_id,
        "elapsed": elapsed,
    }


def process_task(
    assistx: AssistXClient,
    task: Dict[str, Any],
) -> None:
    task_id = task.get("id")
    title = task.get("title", task.get("kind", f"Task {task_id}"))
    description = task.get("description", task.get("text", ""))

    session_id = uuid.uuid4().hex
    logger.info("Processing task %s: %s", task_id, title)

    if not assistx.claim_task(task_id, session_id):
        return

    context = assistx.get_context(task_id, title)
    context_refs = context.get("references", [])
    context_text = ""
    if context_refs:
        snippets = []
        for ref in context_refs[:10]:
            snippet = ref.get("snippet", "")
            if snippet:
                snippets.append(f"- {snippet[:200]}")
        if snippets:
            context_text = "Relevant context:\n" + "\n".join(snippets)

    prompt = f"""Task: {title}

Description: {description}

{context_text}

Please complete this task. Provide your response with:
1. A brief summary of what you did
2. Any important findings or decisions
3. The final result or output"""

    assistx.heartbeat(task_id, session_id)

    result = run_hermes(prompt)

    hermes_session_id = result.get("session_id")
    if hermes_session_id:
        assistx.register_session(session_id, hermes_session_id)

    if result["success"]:
        output = result["output"]
        assistx.write_memory(
            kind="task_result",
            text=output[:2000],
            source=f"hermes:{AGENT_ID}",
            task_id=task_id,
            session_id=session_id,
        )
        assistx.complete_task(
            task_id=task_id,
            session_id=session_id,
            status="DONE",
            summary=output[:500],
            result={
                "output": output[:50000],
                "elapsed": result.get("elapsed"),
                "hermes_session_id": hermes_session_id,
            },
        )
        logger.info("Task %s completed successfully", task_id)
    else:
        error = result.get("error", "unknown")
        output = result.get("output", "")
        assistx.complete_task(
            task_id=task_id,
            session_id=session_id,
            status="FAILED",
            summary=f"Hermes error: {error}",
            result={
                "error": error,
                "output": output[:50000],
                "stderr": result.get("stderr", ""),
                "elapsed": result.get("elapsed"),
            },
        )
        logger.info("Task %s failed: %s", task_id, error)


def run_loop(once: bool = False) -> None:
    logger.info(
        "Hermes agent adapter starting (agent=%s, capabilities=%s, poll_interval=%ds)",
        AGENT_ID,
        AGENT_CAPABILITIES,
        POLL_INTERVAL,
    )

    assistx = AssistXClient()
    consecutive_empty = 0

    while True:
        try:
            tasks = assistx.poll_tasks(limit=MAX_TASKS_PER_LOOP)

            if not tasks:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.info("No tasks available (poll %d)", consecutive_empty)
            else:
                consecutive_empty = 0
                logger.info("Found %d available task(s)", len(tasks))

            for task in tasks:
                try:
                    process_task(assistx, task)
                except requests.HTTPError as e:
                    logger.error("API error on task %s: %s", task.get("id"), e)
                except Exception as e:
                    logger.exception("Unexpected error processing task %s: %s", task.get("id"), e)

            if once:
                logger.info("Single run complete")
                return

            time.sleep(POLL_INTERVAL)

        except requests.ConnectionError:
            logger.warning("Cannot connect to AssistX at %s (retry in %ds)", ASSISTX_URL, POLL_INTERVAL)
            if once:
                return
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            return
        except Exception as e:
            logger.exception("Poll loop error: %s", e)
            if once:
                raise
            time.sleep(POLL_INTERVAL * 2)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("HERMES_LOG_LEVEL", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    once = "--once" in sys.argv
    run_loop(once=once)


if __name__ == "__main__":
    main()
