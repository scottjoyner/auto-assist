from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_PATCH_LOCK = threading.RLock()
_CLIENT_LOCK = threading.RLock()


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _terminate_process(proc: subprocess.Popen[str], grace_seconds: float = 3.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - production executor is Linux
            proc.terminate()
        proc.wait(timeout=max(0.1, grace_seconds))
        return
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
    except ProcessLookupError:
        pass


def install_strict_executor_adapter(adapter: Any) -> None:
    """Install the claim-scoped Hermes executor contract.

    Bootstrap credentials can poll and claim only. A successful claim is exchanged
    for a short-lived task token. The token is supplied only to the Hermes child
    process and is never written into the worker's global environment. Continuous
    heartbeats fence execution; lease loss actively terminates the process group and
    prevents authoritative completion.
    """

    if not _truthy("ASSISTX_STRICT_EXECUTOR_AUTH", "true"):
        return
    if getattr(adapter, "_strict_executor_auth_installed", False):
        return

    original_self_task_call = adapter.call_self_task_llm

    def client_init(self, base_url=adapter.ASSISTX_URL, username=None, password=None):
        del username, password
        self.base_url = str(base_url).rstrip("/")
        self.bootstrap_token = os.getenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "").strip()
        if not self.bootstrap_token:
            raise RuntimeError("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN is required in strict executor mode")
        self.task_token = ""
        self.task_claims: dict[str, Any] = {}
        self._heartbeat_stop = threading.Event()
        self._lease_lost = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_failures = 0
        self._active_task_id = ""
        self._active_claim_id = ""
        self._active_session_id = ""
        self._active_process: subprocess.Popen[str] | None = None
        with _CLIENT_LOCK:
            adapter._strict_executor_client = self

    def token_for_path(self, path: str) -> str:
        if (
            path.startswith("/api/agent/tasks")
            or path.endswith("/claim")
            or path.startswith("/api/executor/claims/")
        ):
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

    def mark_lease_lost(self, reason: str) -> None:
        if self._lease_lost.is_set():
            return
        self._lease_lost.set()
        self._heartbeat_stop.set()
        proc = self._active_process
        if proc is not None:
            _terminate_process(proc)
        logger.error(
            "strict executor lease lost task=%s claim=%s reason=%s",
            self._active_task_id,
            self._active_claim_id,
            reason,
        )

    def heartbeat_loop(self) -> None:
        lease_seconds = max(60, int(os.getenv("HERMES_LEASE_SECONDS", "900")))
        interval = min(120, max(15, lease_seconds // 3))
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
                    mark_lease_lost(self, "heartbeat_failure_budget_exhausted")
                    return

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
        proc = self._active_process
        if proc is not None and proc.poll() is None:
            _terminate_process(proc)
        self._active_process = None
        self.task_token = ""
        self.task_claims = {}
        self._active_task_id = ""
        self._active_claim_id = ""
        self._active_session_id = ""

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
            if (
                str(claims.get("task_id") or "") != task_id
                or str(claims.get("claim_id") or "") != claim_id
                or str(claims.get("agent_id") or "") != adapter.AGENT_ID
            ):
                raise RuntimeError("AssistX issued a token for a different claim identity")
            self.task_token = token
            self.task_claims = claims
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

    def strict_run_hermes(
        prompt: str,
        timeout: int | None = None,
        model: str | None = None,
        provider: str | None = None,
        toolsets: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        with _CLIENT_LOCK:
            client = getattr(adapter, "_strict_executor_client", None)
        if client is None or not client.task_token:
            return {
                "success": False,
                "error": "executor_task_token_missing",
                "output": "",
                "session_id": None,
                "elapsed": 0.0,
            }
        if client._lease_lost.is_set():
            return {
                "success": False,
                "error": "executor_lease_lost",
                "output": "",
                "session_id": None,
                "elapsed": 0.0,
            }

        effective_timeout = int(timeout or getattr(adapter, "HERMES_TIMEOUT", 300))
        cmd = [
            adapter.HERMES_BIN,
            "chat",
            "-q",
            prompt,
            "--quiet",
            "--pass-session-id",
            "--max-turns",
            str(max(1, int(os.getenv("HERMES_MAX_TURNS", "20")))),
        ]
        selected_model = model or getattr(adapter, "HERMES_MODEL", None)
        selected_provider = provider or getattr(adapter, "HERMES_PROVIDER", None)
        if selected_model:
            cmd += ["-m", selected_model]
        if selected_provider:
            cmd += ["--provider", selected_provider]

        env = dict(os.environ)
        env["HERMES_ACCEPT_HOOKS"] = "1"
        env["HERMES_EXECUTOR_TOKEN"] = client.task_token
        env["OPENAI_API_KEY"] = client.task_token
        for protected_name in (
            "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN",
            "ASSISTX_EXECUTOR_SERVICE_TOKEN",
            "ASSISTX_EXECUTOR_SIGNING_KEY_FILE",
            "ASSISTX_EXECUTOR_SIGNING_KEY_PEM",
            "AUTO_ROUTER_ADMIN_TOKEN",
            "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN",
            "ASSISTX_IMPROVEMENT_ATTESTATION_SECRET",
            "ASSISTX_IMPROVEMENT_VERIFY_KEYS",
            "ASSISTX_REPOSITORY_ROOTS_JSON",
            "ASSISTX_IMPROVEMENT_WORKTREE_ROOT",
        ):
            env.pop(protected_name, None)
        if toolsets:
            env["HERMES_TOOLSETS"] = toolsets

        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=cwd,
                start_new_session=(os.name == "posix"),
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error": "hermes_binary_not_found",
                "output": "",
                "session_id": None,
                "elapsed": 0.0,
            }
        client._active_process = proc
        error = ""
        while proc.poll() is None:
            if client._lease_lost.wait(0.25):
                error = "executor_lease_lost"
                _terminate_process(proc)
                break
            if time.monotonic() - started >= effective_timeout:
                error = "timeout"
                _terminate_process(proc)
                break
        stdout, stderr = proc.communicate()
        client._active_process = None
        elapsed = time.monotonic() - started
        session_id = None
        for line in (stdout or "").splitlines():
            if "session_id:" in line or "Session ID:" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    session_id = parts[1].strip()
                    break
        if error:
            return {
                "success": False,
                "error": error,
                "output": stdout or "",
                "stderr": stderr or "",
                "session_id": session_id,
                "elapsed": elapsed,
            }
        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"exit_code_{proc.returncode}",
                "output": stdout or "",
                "stderr": stderr or "",
                "session_id": session_id,
                "elapsed": elapsed,
            }
        return {
            "success": True,
            "output": (stdout or "").strip(),
            "session_id": session_id,
            "elapsed": elapsed,
        }

    def strict_self_task_call(*args, **kwargs):
        if not _truthy("HERMES_SELFTASK_ENABLED", "false"):
            return {
                "success": False,
                "error": "self_tasks_disabled_in_strict_executor",
                "output": "",
                "elapsed": 0.0,
            }
        with _CLIENT_LOCK:
            client = getattr(adapter, "_strict_executor_client", None)
        if client is None or not client.task_token or client._lease_lost.is_set():
            return {
                "success": False,
                "error": "self_task_requires_active_claim",
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
