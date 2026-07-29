from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


RUNBOOK_VERSION = 1
ALLOWED_ACTIONS = {
    "health_check",
    "drain_node",
    "resume_node",
    "restart_service",
    "redeploy_service",
    "reload_model",
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


def build_runbook(plan: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    """Translate a control-plane recovery plan into allowlisted node steps."""
    action = str(plan.get("action") or "collect_evidence")
    node_id = str(plan.get("node_id") or "")
    model_id = plan.get("model_id")
    parameters = dict(plan.get("parameters") or {})
    steps: list[dict[str, Any]]
    if action == "restore_service":
        steps = [
            {"action": "health_check", "parameters": {"stop_runbook_if_ok": True, "continue_if_failed": True}},
            {"action": "restart_service", "parameters": {"service_alias": "inference"}},
            {"action": "health_check", "parameters": {"retries": 5, "retry_delay_seconds": 2}},
        ]
    elif action == "refresh_agent":
        steps = [{"action": "restart_service", "parameters": {"service_alias": "fleet_agent"}}]
    elif action == "reload_model":
        steps = [{"action": "reload_model", "parameters": {"model_id": model_id or parameters.get("model_id")}}]
    elif action in {"drain_and_test", "drain_and_benchmark"}:
        steps = [
            {"action": "drain_node", "parameters": {"reason": action}},
            {"action": "health_check", "parameters": {}},
        ]
    elif action in ALLOWED_ACTIONS:
        steps = [{"action": action, "parameters": parameters}]
    else:
        steps = [{"action": "health_check", "parameters": {}}]
    canonical = {
        "version": RUNBOOK_VERSION,
        "proposal_id": proposal_id,
        "target_node_id": node_id,
        "steps": steps,
        "timeout_seconds": 600,
        "verification": list(plan.get("verify_after") or ["service_online", "report_fresh"]),
        "rollback_policy": "automatic_on_failure",
    }
    canonical["idempotency_key"] = "runbook:" + hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return canonical


class RecoveryRunbookExecutor:
    """Execute typed recovery actions without accepting shell strings."""

    def __init__(
        self,
        *,
        node_id: str,
        lmstudio_url: str | None,
        state_dir: str,
        http: Callable[..., tuple[int, Any]],
        runner: Callable[..., Any] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
        env: dict[str, str] | None = None,
    ):
        self.node_id = node_id
        self.lmstudio_url = (lmstudio_url or "").rstrip("/")
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.http = http
        self.runner = runner
        self.sleeper = sleeper
        self.env = env if env is not None else os.environ

    def execute(self, runbook: dict[str, Any]) -> dict[str, Any]:
        error = validate_runbook(runbook, self.node_id)
        if error:
            return self._outcome(False, "rejected", error, [])
        cached = self._load_cached(runbook["idempotency_key"])
        if cached:
            return {**cached, "idempotent_replay": True}
        started = time.time()
        results, rollbacks = [], []
        for index, step in enumerate(runbook["steps"]):
            if time.time() - started > int(runbook.get("timeout_seconds") or 600):
                result = {"ok": False, "reason": "runbook_timeout"}
            else:
                result = self._execute_step(step)
            results.append({"index": index, "action": step["action"], **result})
            if result.get("ok") and (step.get("parameters") or {}).get("stop_runbook_if_ok"):
                outcome = self._outcome(True, "verified", "", results, verification={"ok": True, "short_circuit": True})
                self._save(runbook["idempotency_key"], outcome)
                return outcome
            if result.get("rollback"):
                rollbacks.append(result["rollback"])
            if not result.get("ok") and not (step.get("parameters") or {}).get("continue_if_failed"):
                rollback_results = self._rollback(reversed(rollbacks))
                outcome = self._outcome(
                    False, "rolled_back" if rollback_results else "failed",
                    result.get("reason") or "step_failed", results,
                    rollback_results=rollback_results,
                )
                self._save(runbook["idempotency_key"], outcome)
                return outcome
        verified = self._verify(runbook)
        if not verified["ok"]:
            rollback_results = self._rollback(reversed(rollbacks))
            outcome = self._outcome(False, "verification_failed", verified["reason"], results, verification=verified, rollback_results=rollback_results)
        else:
            outcome = self._outcome(True, "verified", "", results, verification=verified)
        self._save(runbook["idempotency_key"], outcome)
        return outcome

    def _execute_step(self, step: dict[str, Any]) -> dict[str, Any]:
        action, params = step["action"], step.get("parameters") or {}
        if action == "health_check":
            retries = max(1, min(10, int(params.get("retries") or 1)))
            delay = max(0, min(30, float(params.get("retry_delay_seconds") or 0)))
            result = self._health_check()
            for _ in range(retries - 1):
                if result.get("ok"):
                    break
                self.sleeper(delay)
                result = self._health_check()
            return {**result, "attempts": retries if not result.get("ok") else None}
        if action == "drain_node":
            self._drain_path().write_text(json.dumps({"reason": params.get("reason"), "at": time.time()}))
            return {"ok": True, "rollback": {"action": "resume_node", "parameters": {}}}
        if action == "resume_node":
            self._drain_path().unlink(missing_ok=True)
            return {"ok": True}
        if action == "restart_service":
            return self._restart_service(str(params.get("service_alias") or ""))
        if action == "redeploy_service":
            return self._redeploy(params)
        if action == "reload_model":
            return self._reload_model(str(params.get("model_id") or ""))
        return {"ok": False, "reason": "action_not_allowlisted"}

    def _health_check(self) -> dict[str, Any]:
        if not self.lmstudio_url:
            return {"ok": False, "reason": "inference_url_not_configured"}
        status, body = self.http("GET", f"{self.lmstudio_url}/v1/models", timeout=15)
        models = body.get("data", []) if isinstance(body, dict) else []
        return {"ok": status == 200, "status_code": status, "models_reported": len(models), "reason": "" if status == 200 else "inference_probe_failed"}

    def _restart_service(self, alias: str) -> dict[str, Any]:
        aliases = _json_env(self.env.get("FLEET_RECOVERY_SERVICE_ALIASES", "{}"))
        unit = str(aliases.get(alias) or "")
        if not _SAFE_NAME.fullmatch(unit):
            return {"ok": False, "reason": "service_alias_not_allowlisted"}
        proc = self.runner(["systemctl", "restart", unit], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return {"ok": False, "reason": "service_restart_failed", "stderr": proc.stderr[-1000:]}
        active = self.runner(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
        return {"ok": active.returncode == 0, "unit": unit, "reason": "" if active.returncode == 0 else "service_not_active_after_restart"}

    def _redeploy(self, params: dict[str, Any]) -> dict[str, Any]:
        projects = _json_env(self.env.get("FLEET_RECOVERY_COMPOSE_PROJECTS", "{}"))
        project, service = str(params.get("project") or ""), str(params.get("service") or "")
        root = Path(str(projects.get(project) or "")).resolve()
        if not _SAFE_NAME.fullmatch(project) or not _SAFE_NAME.fullmatch(service) or not root.is_dir():
            return {"ok": False, "reason": "compose_target_not_allowlisted"}
        proc = self.runner(
            ["docker", "compose", "--project-directory", str(root), "up", "-d", "--pull", "always", service],
            capture_output=True, text=True, timeout=600,
        )
        result = {"ok": proc.returncode == 0, "project": project, "service": service, "reason": "" if proc.returncode == 0 else "redeploy_failed", "stderr": proc.stderr[-1000:]}
        rollback = params.get("rollback")
        if proc.returncode == 0 and isinstance(rollback, dict):
            result["rollback"] = {"action": "redeploy_service", "parameters": rollback}
        return result

    def _reload_model(self, model_id: str) -> dict[str, Any]:
        if not model_id or not self.lmstudio_url:
            return {"ok": False, "reason": "model_or_inference_url_missing"}
        status, inventory = self.http("GET", f"{self.lmstudio_url}/api/v1/models", timeout=30)
        if status != 200:
            return {"ok": False, "reason": "native_inventory_failed"}
        rows = inventory.get("models", inventory.get("data", [])) if isinstance(inventory, dict) else []
        instances = [
            row.get("instance_id") or row.get("id")
            for row in rows if str(row.get("model_key") or row.get("path") or row.get("id")) == model_id
        ]
        for instance_id in filter(None, instances):
            self.http("POST", f"{self.lmstudio_url}/api/v1/models/unload", data={"instance_id": instance_id}, timeout=120)
        load_status, loaded = self.http("POST", f"{self.lmstudio_url}/api/v1/models/load", data={"model": model_id}, timeout=300)
        return {"ok": load_status in {200, 201}, "model_id": model_id, "load_response": loaded, "reason": "" if load_status in {200, 201} else "model_reload_failed"}

    def _verify(self, runbook: dict[str, Any]) -> dict[str, Any]:
        checks = runbook.get("verification") or []
        if any(check in {"service_online", "report_fresh"} for check in checks):
            health = self._health_check()
            return {"ok": health["ok"], "checks": checks, "health": health, "reason": health.get("reason", "")}
        return {"ok": True, "checks": checks, "reason": ""}

    def _rollback(self, steps: Any) -> list[dict[str, Any]]:
        return [{"action": step["action"], **self._execute_step(step)} for step in steps]

    def _drain_path(self) -> Path:
        return self.state_dir / "drained.json"

    def _cache_path(self, key: str) -> Path:
        return self.state_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def _load_cached(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        try:
            return json.loads(path.read_text()) if path.exists() else None
        except Exception:
            return None

    def _save(self, key: str, value: dict[str, Any]) -> None:
        self._cache_path(key).write_text(json.dumps(value, sort_keys=True))

    @staticmethod
    def _outcome(ok: bool, status: str, reason: str, steps: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {"ok": ok, "status": status, "reason": reason, "steps": steps, **extra}


def validate_runbook(runbook: dict[str, Any], node_id: str) -> str | None:
    if runbook.get("version") != RUNBOOK_VERSION:
        return "unsupported_runbook_version"
    if runbook.get("target_node_id") != node_id:
        return "runbook_target_mismatch"
    key = str(runbook.get("idempotency_key") or "")
    if not key.startswith("runbook:") or len(key) > 128:
        return "invalid_idempotency_key"
    steps = runbook.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 10:
        return "invalid_runbook_steps"
    for step in steps:
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            return "action_not_allowlisted"
        if not isinstance(step.get("parameters", {}), dict):
            return "invalid_step_parameters"
    timeout = int(runbook.get("timeout_seconds") or 0)
    if timeout < 1 or timeout > 1800:
        return "invalid_runbook_timeout"
    return None


def _json_env(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}
