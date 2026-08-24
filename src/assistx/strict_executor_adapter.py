from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_PATCH_LOCK = threading.RLock()
_ENV_LOCK = threading.RLock()


def install_strict_executor_adapter(adapter: Any) -> None:
    """Replace broad Basic/admin credentials with fenced executor credentials.

    The bootstrap credential can only poll and claim. After a successful claim,
    AssistX issues a short-lived task token used for context, heartbeat,
    completion, and auto-router inference. The patch is installed only when
    ASSISTX_STRICT_EXECUTOR_AUTH is enabled, which is the production default.
    """

    enabled = os.getenv("ASSISTX_STRICT_EXECUTOR_AUTH", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled or getattr(adapter, "_strict_executor_auth_installed", False):
        return

    original_run_hermes = adapter.run_hermes
    original_self_task_call = adapter.call_self_task_llm

    def client_init(self, base_url=adapter.ASSISTX_URL, username=None, password=None):
        del username, password
        self.base_url = str(base_url).rstrip("/")
        self.bootstrap_token = os.getenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "").strip()
        if not self.bootstrap_token:
            raise RuntimeError("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN is required in strict executor mode")
        self.task_token = ""
        self._base_openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self._base_hermes_lm_api_key = os.getenv("HERMES_LM_API_KEY", "")
        self.task_claims: dict[str, Any] = {}
        self._heartbeat_stop = threading.Event()
        self._lease_lost = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_failures = 0
        self._active_task_id = ""
        self._active_claim_id = ""
        self._active_session_id = ""

    def token_for_path(self, path: str) -> str:
        if path.startswith("/api/agent/tasks") or path.endswith("/claim") or path.startswith("/api/executor/claims/"):
            return self.bootstrap_token
        return self.task_token

    def client_request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", 30)
        headers = dict(kwargs.pop("headers", {}) or {})
        token = token_for_path(self, path)
        if not token:
            raise RuntimeError(f"no scoped executor credential is available for {path}")
        headers["Authorization"] = f"Bearer {token}"
        headers["X-AssistX-Agent-Id"] = adapter.AGENT_ID
        response = requests.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._heartbeat_thread = None

    def heartbeat_once(self, status: str = "RUNNING") -> None:
        client_request(
            self,
            "POST",
            f"/api/tasks/{self._active_task_id}/heartbeat",
            json={
                "agent_id": adapter.AGENT_ID,
                "status": status,
                "session_id": self._active_session_id,
                "claim_id": self._active_claim_id,
                "lease_seconds": int(os.getenv("HERMES_LEASE_SECONDS", "900")),
            },
            timeout=15,
        )

    def heartbeat_loop(self) -> None:
        expiry = int(self.task_claims.get("exp") or 0)
        remaining = max(30, expiry - int(time.time())) if expiry else 300
        interval = min(120, max(15, remaining // 3))
        max_failures = max(1, int(os.getenv("HERMES_HEARTBEAT_MAX_FAILURES", "3")))
        while not self._heartbeat_stop.wait(interval):
            try:
                heartbeat_once(self)
                self._heartbeat_failures = 0
            except Exception as exc:
                self._heartbeat_failures += 1
                logger.warning(
                    "strict executor heartbeat failed task=%s claim=%s failures=%s: %s",
                    self._active_task_id,
                    self._active_claim_id,
                    self._heartbeat_failures,
                    exc,
                )
                if self._heartbeat_failures >= max_failures:
                    self._lease_lost.set()
                    self._heartbeat_stop.set()
                    logger.error(
                        "strict executor lease considered lost task=%s claim=%s",
                        self._active_task_id,
                        self._active_claim_id,
                    )

    def start_heartbeat(self, task_id: str, session_id: str, claim_id: str) -> None:
        stop_heartbeat(self)
        self._heartbeat_stop = threading.Event()
        self._lease_lost = threading.Event()
        self._heartbeat_failures = 0
        self._active_task_id = task_id
        self._active_claim_id = claim_id
        self._active_session_id = session_id
        heartbeat_once(self)
        self._heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            args=(self,),
            name=f"hermes-lease-{task_id[:16]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def clear_task_credential(self) -> None:
        stop_heartbeat(self)
        self.task_token = ""
        self.task_claims = {}
        self._active_task_id = ""
        self._active_claim_id = ""
        self._active_session_id = ""
        with _ENV_LOCK:
            os.environ.pop("HERMES_EXECUTOR_TOKEN", None)
            current = os.environ.get("OPENAI_API_KEY", "")
            if current and current == getattr(self, "_exported_task_token", ""):
                os.environ["OPENAI_API_KEY"] = getattr(self, "_base_openai_api_key", "")
            current_lm = os.environ.get("HERMES_LM_API_KEY", "")
            if current_lm and current_lm == getattr(self, "_exported_task_token", ""):
                os.environ["HERMES_LM_API_KEY"] = getattr(self, "_base_hermes_lm_api_key", "")
            self._exported_task_token = ""

    def claim_task(self, task_id: str, session_id: str):
        try:
            result = client_request(
                self,
                "POST",
                f"/api/tasks/{task_id}/claim",
                json={
                    "agent_id": adapter.AGENT_ID,
                    "capabilities": adapter.AGENT_CAPABILITIES,
                    "session_id": session_id,
                    "lease_seconds": int(os.getenv("HERMES_LEASE_SECONDS", "900")),
                },
            )
            if not result.get("claimed"):
                return None
            claimed_task = result.get("task") or {}
            claim_id = str(claimed_task.get("claim_id") or result.get("claim_id") or "")
            if not claim_id:
                raise RuntimeError("AssistX claim response did not contain claim_id")
            issued = client_request(
                self,
                "POST",
                f"/api/executor/claims/{task_id}/token",
                json={"agent_id": adapter.AGENT_ID, "claim_id": claim_id},
            )
            token = str(issued.get("token") or "")
            claims = dict(issued.get("claims") or {})
            if not token:
                raise RuntimeError("AssistX did not issue an executor task token")
            self.task_token = token
            self.task_claims = claims
            self._exported_task_token = token
            with _ENV_LOCK:
                os.environ["HERMES_EXECUTOR_TOKEN"] = token
                # Hermes custom/OpenAI-compatible providers conventionally read
                # OPENAI_API_KEY. This value is task-scoped and removed afterward.
                os.environ["OPENAI_API_KEY"] = token
                # Hermes's provider prefers HERMES_LM_API_KEY when configured;
                # strict execution must use the same claim-scoped JWT there too.
                os.environ["HERMES_LM_API_KEY"] = token
            logger.info(
                "executor token claims: aliases=%r generation=%r task=%s",
                claims.get("allowed_model_aliases"),
                claims.get("projection_generation"),
                task_id,
            )
            start_heartbeat(self, task_id, session_id, claim_id)
            return result
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                logger.info("Task %s already claimed or token issuance was fenced", task_id)
                clear_task_credential(self)
                return None
            raise

    def get_context(self, task_id: str, query: str):
        if self._lease_lost.is_set():
            return {}
        try:
            result = client_request(
                self,
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
        except requests.RequestException as exc:
            logger.warning("Context lookup failed for task %s: %s", task_id, exc)
            return {}

    def heartbeat(self, task_id: str, session_id: str, claim_id: str, status: str = "RUNNING") -> None:
        if (
            task_id != self._active_task_id
            or claim_id != self._active_claim_id
            or session_id != self._active_session_id
        ):
            raise RuntimeError("heartbeat identity does not match active executor claim")
        heartbeat_once(self, status)

    def complete_task(
        self,
        task_id: str,
        session_id: str,
        status: str,
        summary=None,
        result=None,
        claim_id=None,
    ) -> None:
        try:
            stop_heartbeat(self)
            if self._lease_lost.is_set():
                logger.error(
                    "refusing authoritative completion after lease loss task=%s claim=%s",
                    task_id,
                    claim_id,
                )
                return
            client_request(
                self,
                "POST",
                f"/api/tasks/{task_id}/complete",
                json={
                    "agent_id": adapter.AGENT_ID,
                    "status": status,
                    "summary": summary or "",
                    "result": result or {},
                    "session_id": session_id,
                    "claim_id": claim_id,
                },
            )
        finally:
            clear_task_credential(self)

    def strict_run_hermes(*args, **kwargs):
        client_token = os.getenv("HERMES_EXECUTOR_TOKEN", "").strip()
        if not client_token:
            return {
                "success": False,
                "error": "executor_task_token_missing",
                "output": "",
                "session_id": None,
                "elapsed": 0.0,
            }
        return original_run_hermes(*args, **kwargs)

    def strict_self_task_call(*args, **kwargs):
        if os.getenv("HERMES_SELFTASK_ENABLED", "false").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return {
                "success": False,
                "error": "self_tasks_disabled_in_strict_executor",
                "output": "",
                "elapsed": 0.0,
            }
        if not os.getenv("HERMES_EXECUTOR_TOKEN", "").strip():
            return {
                "success": False,
                "error": "self_task_requires_claim_scoped_token",
                "output": "",
                "elapsed": 0.0,
            }
        return original_self_task_call(*args, **kwargs)

    with _PATCH_LOCK:
        adapter.AssistXClient.__init__ = client_init
        adapter.AssistXClient._request = client_request
        adapter.AssistXClient.claim_task = claim_task
        adapter.AssistXClient.get_context = get_context
        adapter.AssistXClient.heartbeat = heartbeat
        adapter.AssistXClient.complete_task = complete_task
        adapter.run_hermes = strict_run_hermes
        adapter.call_self_task_llm = strict_self_task_call
        adapter._strict_executor_auth_installed = True
