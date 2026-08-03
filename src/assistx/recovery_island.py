from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

RECOVERY_ACTIVATION_VERSION = 1
RECOVERY_ISLAND_RUNBOOK_VERSION = 1
RECOVERY_ISLAND_ACTIONS = {
    "stage",
    "verify",
    "activate",
    "deactivate",
}
_MAX_ACTIVATION_TTL_SECONDS = 3600
_MAX_RUNBOOK_TTL_SECONDS = 1800
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
_ALLOWED_FENCE_PREFIXES = (
    "assistx-lease:",
    "manual-break-glass:",
    "witness:",
)


def _canonical_attested_payload(value: dict[str, Any]) -> bytes:
    payload = dict(value)
    attestation = dict(payload.get("attestation") or {})
    attestation.pop("signature", None)
    payload["attestation"] = attestation
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def build_recovery_island_runbook(
    *,
    action: str,
    node_id: str,
    deployment: str,
    parameters: dict[str, Any] | None = None,
    proposal_id: str = "",
) -> dict[str, Any]:
    if action not in RECOVERY_ISLAND_ACTIONS:
        raise ValueError("unsupported recovery-island action")
    canonical = {
        "version": RECOVERY_ISLAND_RUNBOOK_VERSION,
        "proposal_id": str(proposal_id),
        "target_node_id": str(node_id),
        "deployment": str(deployment),
        "action": action,
        "parameters": dict(parameters or {}),
        "timeout_seconds": 900,
    }
    canonical["idempotency_key"] = "recovery-island:" + hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return canonical


def sign_recovery_island_runbook(
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
        "key_id": str(key_id),
        "issued_at": issued_at,
        "expires_at": issued_at
        + max(30, min(int(ttl_seconds), _MAX_RUNBOOK_TTL_SECONDS)),
        "nonce": nonce or hashlib.sha256(os.urandom(32)).hexdigest(),
    }
    signed["attestation"]["signature"] = hmac.new(
        secret.encode("utf-8"),
        _canonical_attested_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def sign_recovery_activation(
    activation: dict[str, Any],
    *,
    key_id: str,
    secret: str,
    ttl_seconds: int = 900,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Sign the distinct privilege required to activate a recovery island.

    Recovery runbooks and recovery activation use separate keys. A normal signed
    runbook may stage, inspect, and verify a recovery deployment, but it cannot
    advertise the deployment as active unless this envelope also verifies.
    """

    issued_at = int(now if now is not None else time.time())
    signed = dict(activation)
    signed.setdefault("version", RECOVERY_ACTIVATION_VERSION)
    signed.setdefault("mode", "activate")
    signed["attestation"] = {
        "algorithm": "hmac-sha256",
        "key_id": str(key_id),
        "issued_at": issued_at,
        "expires_at": issued_at
        + max(30, min(int(ttl_seconds), _MAX_ACTIVATION_TTL_SECONDS)),
        "nonce": nonce or hashlib.sha256(os.urandom(32)).hexdigest(),
    }
    signed["attestation"]["signature"] = hmac.new(
        secret.encode("utf-8"),
        _canonical_attested_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def verify_recovery_activation(
    envelope: dict[str, Any],
    keys: dict[str, str],
    *,
    node_id: str,
    deployment: str,
    bundle_sha256: str,
    minimum_epoch: int = 0,
    now: int | None = None,
) -> str | None:
    if not isinstance(envelope, dict):
        return "missing_recovery_activation"
    if envelope.get("version") != RECOVERY_ACTIVATION_VERSION:
        return "unsupported_recovery_activation_version"
    if envelope.get("mode") != "activate":
        return "recovery_activation_mode_mismatch"
    if str(envelope.get("target_node_id") or "") != node_id:
        return "recovery_activation_target_mismatch"
    if str(envelope.get("deployment") or "") != deployment:
        return "recovery_activation_deployment_mismatch"
    supplied_bundle = str(envelope.get("bundle_sha256") or "")
    if not _SHA256.fullmatch(supplied_bundle):
        return "invalid_recovery_bundle_sha256"
    if supplied_bundle != bundle_sha256:
        return "recovery_activation_bundle_mismatch"
    try:
        epoch = int(envelope.get("epoch") or 0)
    except (TypeError, ValueError):
        return "invalid_recovery_activation_epoch"
    if epoch <= int(minimum_epoch):
        return "stale_recovery_activation_epoch"
    fence_proof = str(envelope.get("fence_proof") or "")
    if not fence_proof.startswith(_ALLOWED_FENCE_PREFIXES):
        return "missing_recovery_fence_proof"

    attestation = envelope.get("attestation")
    if not isinstance(attestation, dict):
        return "missing_recovery_activation_attestation"
    if attestation.get("algorithm") != "hmac-sha256":
        return "unsupported_recovery_activation_algorithm"
    key_id = str(attestation.get("key_id") or "")
    secret = keys.get(key_id)
    if not secret:
        return "unknown_recovery_activation_key"
    current = int(now if now is not None else time.time())
    try:
        issued_at = int(attestation.get("issued_at") or 0)
        expires_at = int(attestation.get("expires_at") or 0)
    except (TypeError, ValueError):
        return "invalid_recovery_activation_window"
    if issued_at > current + 30:
        return "recovery_activation_issued_in_future"
    if (
        expires_at <= current
        or expires_at - issued_at < 30
        or expires_at - issued_at > _MAX_ACTIVATION_TTL_SECONDS
    ):
        return "recovery_activation_expired_or_invalid"
    nonce = str(attestation.get("nonce") or "")
    if len(nonce) < 32 or len(nonce) > 128:
        return "invalid_recovery_activation_nonce"
    supplied = str(attestation.get("signature") or "")
    expected = hmac.new(
        secret.encode("utf-8"),
        _canonical_attested_payload(envelope),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return "recovery_activation_signature_mismatch"
    return None


def verify_recovery_island_runbook(
    runbook: dict[str, Any],
    keys: dict[str, str],
    *,
    node_id: str,
    now: int | None = None,
) -> str | None:
    if not isinstance(runbook, dict):
        return "missing_recovery_island_runbook"
    if runbook.get("version") != RECOVERY_ISLAND_RUNBOOK_VERSION:
        return "unsupported_recovery_island_runbook_version"
    if str(runbook.get("target_node_id") or "") != node_id:
        return "recovery_island_target_mismatch"
    if str(runbook.get("action") or "") not in RECOVERY_ISLAND_ACTIONS:
        return "recovery_island_action_not_allowlisted"
    if not _SAFE_NAME.fullmatch(str(runbook.get("deployment") or "")):
        return "invalid_recovery_island_deployment"
    if not isinstance(runbook.get("parameters") or {}, dict):
        return "invalid_recovery_island_parameters"
    key = str(runbook.get("idempotency_key") or "")
    if not key.startswith("recovery-island:") or len(key) > 128:
        return "invalid_recovery_island_idempotency_key"
    try:
        timeout = int(runbook.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        return "invalid_recovery_island_timeout"
    if timeout < 1 or timeout > 1800:
        return "invalid_recovery_island_timeout"

    attestation = runbook.get("attestation")
    if not isinstance(attestation, dict):
        return "missing_recovery_island_attestation"
    if attestation.get("algorithm") != "hmac-sha256":
        return "unsupported_recovery_island_attestation_algorithm"
    secret = keys.get(str(attestation.get("key_id") or ""))
    if not secret:
        return "unknown_recovery_island_attestation_key"
    current = int(now if now is not None else time.time())
    try:
        issued_at = int(attestation.get("issued_at") or 0)
        expires_at = int(attestation.get("expires_at") or 0)
    except (TypeError, ValueError):
        return "invalid_recovery_island_attestation_window"
    if issued_at > current + 30:
        return "recovery_island_attestation_issued_in_future"
    if expires_at <= current or expires_at - issued_at > _MAX_RUNBOOK_TTL_SECONDS:
        return "recovery_island_attestation_expired_or_invalid"
    nonce = str(attestation.get("nonce") or "")
    if len(nonce) < 32 or len(nonce) > 128:
        return "invalid_recovery_island_attestation_nonce"
    supplied = str(attestation.get("signature") or "")
    expected = hmac.new(
        secret.encode("utf-8"),
        _canonical_attested_payload(runbook),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return "recovery_island_runbook_signature_mismatch"
    return None


def private_health_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower().strip("[]").rstrip(".")
    if host in {"localhost", "host.docker.internal", "gateway.docker.internal"}:
        return True
    if host.endswith((".lan", ".local", ".internal", ".ts.net")):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def json_mapping(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RecoveryIslandExecutor:
    """Stage and activate a local recovery deployment without shell input.

    The executor is intended for an isolated Beelink or similar recovery node. It
    has no discovery, scheduling, or routing authority. All mutable actions are
    bounded by a static deployment allowlist and signed, replay-protected inputs.
    """

    def __init__(
        self,
        *,
        node_id: str,
        state_dir: str,
        http: Callable[..., tuple[int, Any]],
        runner: Callable[..., Any] = subprocess.run,
        env: dict[str, str] | None = None,
        runbook_keys: dict[str, str] | None = None,
        activation_keys: dict[str, str] | None = None,
    ) -> None:
        self.node_id = node_id
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.http = http
        self.runner = runner
        self.env = env if env is not None else os.environ
        self.runbook_keys = runbook_keys if runbook_keys is not None else json_mapping(
            self.env.get("FLEET_RUNBOOK_VERIFY_KEYS", "{}")
        )
        self.activation_keys = (
            activation_keys
            if activation_keys is not None
            else json_mapping(
                self.env.get("FLEET_RECOVERY_ACTIVATION_VERIFY_KEYS", "{}")
            )
        )

    def execute(self, runbook: dict[str, Any]) -> dict[str, Any]:
        error = verify_recovery_island_runbook(
            runbook,
            self.runbook_keys,
            node_id=self.node_id,
        )
        if error:
            return self._outcome(False, "rejected", error)
        cached = self._load_cached(str(runbook["idempotency_key"]))
        if cached:
            return {**cached, "idempotent_replay": True}
        nonce = str(runbook["attestation"]["nonce"])
        if not self._claim_nonce("runbook", nonce):
            return self._outcome(False, "rejected", "recovery_island_replay_detected")

        deployment = str(runbook["deployment"])
        parameters = dict(runbook.get("parameters") or {})
        action = str(runbook["action"])
        try:
            config = self._deployment_config(deployment)
            if action == "stage":
                result = self._stage(deployment, config, parameters)
            elif action == "verify":
                result = self._verify(deployment, config)
            elif action == "activate":
                result = self._activate(deployment, config, parameters)
            else:
                result = self._deactivate(deployment, config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = self._outcome(False, "failed", str(exc))

        self._save_cached(str(runbook["idempotency_key"]), result)
        return result

    def activate_from_envelope(
        self,
        deployment: str,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        """Manual/witness break-glass entry when the primary AssistX API is gone."""

        try:
            config = self._deployment_config(deployment)
            return self._activate(
                deployment,
                config,
                {"activation": envelope},
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._outcome(False, "failed", str(exc))

    def status(self, deployment: str) -> dict[str, Any]:
        try:
            config = self._deployment_config(deployment)
        except ValueError as exc:
            return self._outcome(False, "invalid", str(exc))
        prepared = self._read_json(self._prepared_path(deployment))
        active = self._read_json(self._active_path(deployment))
        verification = self._verify(deployment, config) if active else None
        return {
            "ok": True,
            "status": "active" if active else "prepared" if prepared else "empty",
            "node_id": self.node_id,
            "deployment": deployment,
            "prepared": prepared,
            "active": active,
            "verification": verification,
        }

    def _deployment_config(self, deployment: str) -> dict[str, Any]:
        deployments = json_mapping(
            self.env.get("FLEET_RECOVERY_ISLAND_DEPLOYMENTS", "{}")
        )
        raw = deployments.get(deployment)
        if not isinstance(raw, dict):
            raise ValueError("recovery_island_deployment_not_allowlisted")
        root = Path(str(raw.get("project_directory") or "")).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("recovery_island_project_directory_missing")
        project_name = str(raw.get("project_name") or deployment)
        if not _SAFE_NAME.fullmatch(project_name):
            raise ValueError("invalid_recovery_island_project_name")
        compose_values = raw.get("compose_files") or ["compose.recovery.yml"]
        if not isinstance(compose_values, list) or not compose_values:
            raise ValueError("recovery_island_compose_files_required")
        compose_files: list[Path] = []
        for value in compose_values:
            candidate = (root / str(value)).resolve()
            if root not in candidate.parents and candidate != root:
                raise ValueError("recovery_island_compose_path_escape")
            if not candidate.is_file():
                raise ValueError("recovery_island_compose_file_missing")
            compose_files.append(candidate)
        services = [str(value) for value in raw.get("services") or []]
        if not services or any(not _SAFE_NAME.fullmatch(value) for value in services):
            raise ValueError("invalid_recovery_island_services")
        bundle_path = Path(str(raw.get("bundle_path") or "")).expanduser().resolve()
        manifest_path = Path(str(raw.get("manifest_path") or "")).expanduser().resolve()
        expected_sha = str(raw.get("bundle_sha256") or "")
        if not bundle_path.is_file() or not manifest_path.is_file():
            raise ValueError("recovery_island_bundle_missing")
        if not _SHA256.fullmatch(expected_sha):
            raise ValueError("invalid_recovery_island_bundle_sha256")
        health_urls = [str(value) for value in raw.get("health_urls") or []]
        if not health_urls or any(not private_health_url(value) for value in health_urls):
            raise ValueError("invalid_recovery_island_health_urls")
        return {
            **raw,
            "root": root,
            "project_name": project_name,
            "compose_files_resolved": compose_files,
            "services": services,
            "bundle_path_resolved": bundle_path,
            "manifest_path_resolved": manifest_path,
            "bundle_sha256": expected_sha,
            "health_urls": health_urls,
        }

    def _compose_command(self, config: dict[str, Any]) -> list[str]:
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(config["root"]),
            "--project-name",
            str(config["project_name"]),
        ]
        for compose_file in config["compose_files_resolved"]:
            command.extend(["-f", str(compose_file)])
        return command

    def _stage(
        self,
        deployment: str,
        config: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        expected_sha = str(parameters.get("bundle_sha256") or config["bundle_sha256"])
        if expected_sha != config["bundle_sha256"]:
            return self._outcome(False, "rejected", "recovery_bundle_not_allowlisted")
        actual_sha = sha256_file(config["bundle_path_resolved"])
        if actual_sha != expected_sha:
            return self._outcome(False, "rejected", "recovery_bundle_checksum_mismatch")
        manifest = json.loads(config["manifest_path_resolved"].read_text(encoding="utf-8"))
        if str(manifest.get("bundle_sha256") or "") != expected_sha:
            return self._outcome(False, "rejected", "recovery_manifest_bundle_mismatch")

        loaded = self.runner(
            ["docker", "load", "--input", str(config["bundle_path_resolved"])],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if loaded.returncode != 0:
            return self._outcome(
                False,
                "failed",
                "recovery_bundle_load_failed",
                stderr=str(getattr(loaded, "stderr", ""))[-1000:],
            )
        rendered = self.runner(
            [*self._compose_command(config), "config"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if rendered.returncode != 0:
            return self._outcome(
                False,
                "failed",
                "recovery_compose_render_failed",
                stderr=str(getattr(rendered, "stderr", ""))[-1000:],
            )
        prepared = {
            "deployment": deployment,
            "node_id": self.node_id,
            "bundle_sha256": actual_sha,
            "manifest_sha256": sha256_file(config["manifest_path_resolved"]),
            "prepared_at": int(time.time()),
        }
        self._write_json(self._prepared_path(deployment), prepared)
        return self._outcome(True, "prepared", "", evidence=prepared)

    def _activate(
        self,
        deployment: str,
        config: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = self._read_json(self._prepared_path(deployment))
        if not prepared:
            return self._outcome(False, "blocked", "recovery_island_not_prepared")
        bundle_sha = str(prepared.get("bundle_sha256") or "")
        if bundle_sha != config["bundle_sha256"]:
            return self._outcome(False, "blocked", "prepared_recovery_bundle_changed")
        active = self._read_json(self._active_path(deployment))
        minimum_epoch = int((active or {}).get("epoch") or 0)
        activation = parameters.get("activation")
        error = verify_recovery_activation(
            activation if isinstance(activation, dict) else {},
            self.activation_keys,
            node_id=self.node_id,
            deployment=deployment,
            bundle_sha256=bundle_sha,
            minimum_epoch=minimum_epoch,
        )
        if error:
            return self._outcome(False, "blocked", error)
        nonce = str(activation["attestation"]["nonce"])
        if not self._claim_nonce("activation", nonce):
            return self._outcome(False, "blocked", "recovery_activation_replay_detected")

        process_env = dict(self.env)
        process_env["ASSISTX_RECOVERY_ACTIVE"] = "1"
        started = self.runner(
            [
                *self._compose_command(config),
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                *config["services"],
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            env=process_env,
        )
        if started.returncode != 0:
            return self._outcome(
                False,
                "failed",
                "recovery_island_start_failed",
                stderr=str(getattr(started, "stderr", ""))[-1000:],
            )
        active_state = {
            "deployment": deployment,
            "node_id": self.node_id,
            "epoch": int(activation["epoch"]),
            "bundle_sha256": bundle_sha,
            "fence_proof": str(activation["fence_proof"]),
            "activated_at": int(time.time()),
        }
        self._write_json(self._active_path(deployment), active_state)
        verification = self._verify(deployment, config)
        if not verification.get("ok"):
            self._deactivate(deployment, config)
            return self._outcome(
                False,
                "rolled_back",
                "recovery_island_verification_failed",
                verification=verification,
            )
        return self._outcome(
            True,
            "active",
            "",
            active=active_state,
            verification=verification,
            rollback={"action": "deactivate", "deployment": deployment},
        )

    def _deactivate(
        self,
        deployment: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        stopped = self.runner(
            [*self._compose_command(config), "stop", *config["services"]],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if stopped.returncode != 0:
            return self._outcome(
                False,
                "failed",
                "recovery_island_stop_failed",
                stderr=str(getattr(stopped, "stderr", ""))[-1000:],
            )
        self._active_path(deployment).unlink(missing_ok=True)
        return self._outcome(True, "inactive", "")

    def _verify(
        self,
        deployment: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        for url in config["health_urls"]:
            status, body = self.http("GET", url, timeout=15)
            checks.append(
                {
                    "url": url,
                    "status_code": status,
                    "ok": status == 200,
                    "body_type": type(body).__name__,
                }
            )
        ok = bool(checks) and all(item["ok"] for item in checks)
        return self._outcome(
            ok,
            "verified" if ok else "unhealthy",
            "" if ok else "recovery_island_health_check_failed",
            deployment=deployment,
            checks=checks,
        )

    def _claim_nonce(self, kind: str, nonce: str) -> bool:
        nonce_dir = self.state_dir / "nonces" / kind
        nonce_dir.mkdir(parents=True, exist_ok=True)
        path = nonce_dir / hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def _prepared_path(self, deployment: str) -> Path:
        return self.state_dir / f"{deployment}.prepared.json"

    def _active_path(self, deployment: str) -> Path:
        return self.state_dir / f"{deployment}.active.json"

    def _cache_path(self, key: str) -> Path:
        return self.state_dir / (
            hashlib.sha256(key.encode("utf-8")).hexdigest() + ".outcome.json"
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _load_cached(self, key: str) -> dict[str, Any] | None:
        return self._read_json(self._cache_path(key))

    def _save_cached(self, key: str, value: dict[str, Any]) -> None:
        self._write_json(self._cache_path(key), value)

    @staticmethod
    def _outcome(ok: bool, status: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {"ok": ok, "status": status, "reason": reason, **extra}
