from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def sign_runbook(
    runbook: dict[str, Any],
    *,
    key_id: str,
    secret: str,
    ttl_seconds: int = 900,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    issued_at = int(now if now is not None else time.time())
    signed = dict(runbook)
    signed["attestation"] = {
        "algorithm": "hmac-sha256",
        "key_id": key_id,
        "issued_at": issued_at,
        "expires_at": issued_at + max(30, min(ttl_seconds, 1800)),
        "nonce": nonce or hashlib.sha256(os.urandom(32)).hexdigest(),
    }
    signed["attestation"]["signature"] = hmac.new(
        secret.encode(),
        _canonical_signed_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def verify_runbook_attestation(
    runbook: dict[str, Any],
    keys: dict[str, str],
    *,
    now: int | None = None,
) -> str | None:
    attestation = runbook.get("attestation")
    if not isinstance(attestation, dict):
        return "missing_runbook_attestation"
    if attestation.get("algorithm") != "hmac-sha256":
        return "unsupported_attestation_algorithm"
    key_id = str(attestation.get("key_id") or "")
    secret = keys.get(key_id)
    if not secret:
        return "unknown_attestation_key"
    current = int(now if now is not None else time.time())
    issued_at = int(attestation.get("issued_at") or 0)
    expires_at = int(attestation.get("expires_at") or 0)
    if issued_at > current + 30:
        return "attestation_issued_in_future"
    if expires_at <= current or expires_at - issued_at > 1800:
        return "attestation_expired_or_invalid"
    nonce = str(attestation.get("nonce") or "")
    if len(nonce) < 32 or len(nonce) > 128:
        return "invalid_attestation_nonce"
    supplied = str(attestation.get("signature") or "")
    expected = hmac.new(secret.encode(), _canonical_signed_payload(runbook), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return "runbook_signature_mismatch"
    return None


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
        verify_keys: dict[str, str] | None = None,
    ):
        self.node_id = node_id
        self.lmstudio_url = (lmstudio_url or "").rstrip("/")
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.http = http
        self.runner = runner
        self.sleeper = sleeper
        self.env = env if env is not None else os.environ
        self.verify_keys = verify_keys if verify_keys is not None else _json_env(
            self.env.get("FLEET_RUNBOOK_VERIFY_KEYS", "{}")
        )

    def execute(self, runbook: dict[str, Any]) -> dict[str, Any]:
        error = validate_runbook(runbook, self.node_id)
        if error:
            return self._outcome(False, "rejected", error, [])
        attestation_error = verify_runbook_attestation(runbook, self.verify_keys)
        if attestation_error:
            return self._outcome(False, "rejected", attestation_error, [])
        cached = self._load_cached(runbook["idempotency_key"])
        if cached:
            return {**cached, "idempotent_replay": True}
        if not self._claim_nonce(str(runbook["attestation"]["nonce"])):
            return self._outcome(False, "rejected", "attestation_replay_detected", [])
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
            was_drained = self._drain_path().exists()
            self._drain_path().write_text(json.dumps({"reason": params.get("reason"), "at": time.time()}))
            result = {"ok": True, "pre_state": {"drained": was_drained}}
            if not was_drained:
                result["rollback"] = {"action": "resume_node", "parameters": {}}
            return result
        if action == "resume_node":
            self._drain_path().unlink(missing_ok=True)
            return {"ok": True}
        if action == "restart_service":
            return self._restart_service(str(params.get("service_alias") or ""))
        if action == "stop_service":
            unit = str(params.get("unit") or "")
            if not _SAFE_NAME.fullmatch(unit):
                return {"ok": False, "reason": "invalid_rollback_service"}
            proc = self.runner(
                ["systemctl", "stop", unit],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {"ok": proc.returncode == 0, "unit": unit, "reason": "" if proc.returncode == 0 else "service_stop_rollback_failed"}
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
        config = aliases.get(alias)
        if isinstance(config, str):
            config = {"adapter": "systemd", "unit": config}
        if not isinstance(config, dict):
            return {"ok": False, "reason": "service_alias_not_allowlisted"}
        adapter = str(config.get("adapter") or "")
        if adapter == "observation":
            return {
                "ok": False,
                "reason": "observation_only_adapter",
                "adapter": adapter,
                "operator_instructions": config.get("instructions"),
            }
        if adapter == "launchd":
            label = str(config.get("label") or "")
            if not _SAFE_NAME.fullmatch(label):
                return {"ok": False, "reason": "launchd_label_not_allowlisted"}
            proc = self.runner(
                ["launchctl", "kickstart", "-k", f"system/{label}"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            verified = self.runner(
                ["launchctl", "print", f"system/{label}"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "ok": proc.returncode == 0 and verified.returncode == 0,
                "adapter": adapter,
                "label": label,
                "reason": "" if proc.returncode == 0 and verified.returncode == 0 else "launchd_restart_failed",
            }
        if adapter == "docker_compose":
            projects = _json_env(self.env.get("FLEET_RECOVERY_COMPOSE_PROJECTS", "{}"))
            project, service = str(config.get("project") or ""), str(config.get("service") or "")
            root = Path(str(projects.get(project) or "")).resolve()
            if not _SAFE_NAME.fullmatch(project) or not _SAFE_NAME.fullmatch(service) or not root.is_dir():
                return {"ok": False, "reason": "compose_target_not_allowlisted"}
            proc = self.runner(
                ["docker", "compose", "--project-directory", str(root), "restart", service],
                capture_output=True,
                text=True,
                timeout=180,
            )
            return {
                "ok": proc.returncode == 0,
                "adapter": adapter,
                "project": project,
                "service": service,
                "reason": "" if proc.returncode == 0 else "compose_restart_failed",
            }
        if adapter != "systemd":
            return {"ok": False, "reason": "recovery_adapter_not_supported"}
        unit = str(config.get("unit") or "")
        if not _SAFE_NAME.fullmatch(unit):
            return {"ok": False, "reason": "service_alias_not_allowlisted"}
        previous = self.runner(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
        was_active = previous.returncode == 0
        proc = self.runner(["systemctl", "restart", unit], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return {"ok": False, "reason": "service_restart_failed", "stderr": proc.stderr[-1000:], "pre_state": {"active": was_active}}
        active = self.runner(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
        result = {"ok": active.returncode == 0, "adapter": adapter, "unit": unit, "reason": "" if active.returncode == 0 else "service_not_active_after_restart", "pre_state": {"active": was_active}}
        if not was_active:
            result["rollback"] = {"action": "stop_service", "parameters": {"unit": unit}}
        return result

    def _redeploy(self, params: dict[str, Any]) -> dict[str, Any]:
        projects = _json_env(self.env.get("FLEET_RECOVERY_COMPOSE_PROJECTS", "{}"))
        project, service = str(params.get("project") or ""), str(params.get("service") or "")
        root = Path(str(projects.get(project) or "")).resolve()
        image_digest = str(params.get("image_digest") or "")
        image_env = str(params.get("image_env") or "")
        if (
            not _SAFE_NAME.fullmatch(project)
            or not _SAFE_NAME.fullmatch(service)
            or not _SAFE_NAME.fullmatch(image_env)
            or not root.is_dir()
            or "@sha256:" not in image_digest
        ):
            return {"ok": False, "reason": "compose_target_not_allowlisted"}
        container = self.runner(
            ["docker", "compose", "--project-directory", str(root), "ps", "-q", service],
            capture_output=True, text=True, timeout=30,
        )
        container_id = container.stdout.strip() if container.returncode == 0 else ""
        previous_image = ""
        if container_id:
            inspected = self.runner(
                ["docker", "inspect", "--format", "{{.Config.Image}}", container_id],
                capture_output=True, text=True, timeout=30,
            )
            previous_image = inspected.stdout.strip() if inspected.returncode == 0 else ""
        process_env = dict(self.env)
        process_env[image_env] = image_digest
        proc = self.runner(
            ["docker", "compose", "--project-directory", str(root), "up", "-d", "--no-build", service],
            capture_output=True, text=True, timeout=600, env=process_env,
        )
        result = {
            "ok": proc.returncode == 0,
            "project": project,
            "service": service,
            "image_digest": image_digest,
            "pre_state": {"image": previous_image},
            "reason": "" if proc.returncode == 0 else "redeploy_failed",
            "stderr": proc.stderr[-1000:],
        }
        if proc.returncode == 0 and "@sha256:" in previous_image:
            result["rollback"] = {
                "action": "redeploy_service",
                "parameters": {
                    "project": project,
                    "service": service,
                    "image_digest": previous_image,
                    "image_env": image_env,
                },
            }
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

    def _claim_nonce(self, nonce: str) -> bool:
        nonce_dir = self.state_dir / "nonces"
        nonce_dir.mkdir(parents=True, exist_ok=True)
        path = nonce_dir / hashlib.sha256(nonce.encode()).hexdigest()
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            return True
        except FileExistsError:
            return False

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


def _canonical_signed_payload(runbook: dict[str, Any]) -> bytes:
    payload = dict(runbook)
    attestation = dict(payload.get("attestation") or {})
    attestation.pop("signature", None)
    payload["attestation"] = attestation
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
