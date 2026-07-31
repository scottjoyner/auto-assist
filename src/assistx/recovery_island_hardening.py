from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .recovery_island import RecoveryIslandExecutor, verify_recovery_activation

_SAFE_PROCESS_TOKEN = re.compile(r"^[A-Za-z0-9_.+ -]{1,100}$")
_SAFE_DEPLOYMENT = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


class HardenedRecoveryIslandExecutor(RecoveryIslandExecutor):
    """Production durability and host-resource checks over the bounded executor."""

    def execute(self, runbook: dict[str, Any]) -> dict[str, Any]:
        try:
            return super().execute(runbook)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            result = self._outcome(False, "failed", str(exc))
            key = str(runbook.get("idempotency_key") or "")
            if key:
                self._save_cached(key, result)
            return result

    def activate_from_envelope(
        self,
        deployment: str,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return super().activate_from_envelope(deployment, envelope)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            return self._outcome(False, "failed", str(exc))

    def status(self, deployment: str) -> dict[str, Any]:
        result = super().status(deployment)
        result["activation_epoch"] = self._read_json(self._epoch_path(deployment))
        return result

    def _stage(
        self,
        deployment: str,
        config: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        result = super()._stage(deployment, config, parameters)
        if not result.get("ok"):
            return result
        try:
            manifest = json.loads(
                config["manifest_path_resolved"].read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            self._prepared_path(deployment).unlink(missing_ok=True)
            return self._outcome(
                False,
                "failed",
                "recovery_manifest_unreadable",
                detail=str(exc)[:500],
            )
        images = manifest.get("images")
        if not isinstance(images, list) or not images:
            self._prepared_path(deployment).unlink(missing_ok=True)
            return self._outcome(
                False,
                "rejected",
                "recovery_manifest_images_required",
            )
        missing: list[str] = []
        verified: list[str] = []
        for image in images:
            image_id = (
                str(image.get("id") or "")
                if isinstance(image, dict)
                else ""
            )
            if not image_id:
                missing.append("<missing-id-in-manifest>")
                continue
            inspected = self.runner(
                ["docker", "image", "inspect", image_id],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if inspected.returncode != 0:
                missing.append(image_id)
            else:
                verified.append(image_id)
        if missing:
            self._prepared_path(deployment).unlink(missing_ok=True)
            return self._outcome(
                False,
                "rejected",
                "recovery_manifest_images_missing",
                missing_images=missing,
                verified_images=verified,
            )
        prepared = self._read_json(self._prepared_path(deployment)) or {}
        prepared["verified_image_ids"] = verified
        prepared["image_count"] = len(verified)
        self._write_json(self._prepared_path(deployment), prepared)
        return {
            **result,
            "evidence": prepared,
            "verified_image_ids": verified,
        }

    def _activate(
        self,
        deployment: str,
        config: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = self._read_json(self._prepared_path(deployment))
        if not prepared:
            return self._outcome(
                False,
                "blocked",
                "recovery_island_not_prepared",
            )
        activation = parameters.get("activation")
        if not isinstance(activation, dict):
            return self._outcome(
                False,
                "blocked",
                "missing_recovery_activation",
            )
        epoch_state = self._read_json(self._epoch_path(deployment)) or {}
        active_state = self._read_json(self._active_path(deployment)) or {}
        minimum_epoch = max(
            int(epoch_state.get("epoch") or 0),
            int(active_state.get("epoch") or 0),
        )
        error = verify_recovery_activation(
            activation,
            self.activation_keys,
            node_id=self.node_id,
            deployment=deployment,
            bundle_sha256=str(prepared.get("bundle_sha256") or ""),
            minimum_epoch=minimum_epoch,
        )
        if error:
            return self._outcome(False, "blocked", error)

        preflight = self._resource_preflight(deployment, config)
        if not preflight.get("ok"):
            return preflight

        # Persist the highest accepted epoch before starting containers. A failed
        # or rolled-back activation still consumes its epoch and cannot be replayed.
        epoch_record = {
            "deployment": deployment,
            "node_id": self.node_id,
            "epoch": int(activation["epoch"]),
            "bundle_sha256": str(activation.get("bundle_sha256") or ""),
            "fence_proof": str(activation.get("fence_proof") or ""),
            "resource_preflight": preflight,
        }
        self._write_json(self._epoch_path(deployment), epoch_record)
        return super()._activate(deployment, config, parameters)

    def _resource_preflight(
        self,
        deployment: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        required_active = config.get("requires_active_deployments") or []
        if not isinstance(required_active, list):
            return self._outcome(
                False,
                "rejected",
                "invalid_required_active_deployments",
            )
        for name in required_active:
            candidate = str(name)
            if not _SAFE_DEPLOYMENT.fullmatch(candidate):
                return self._outcome(
                    False,
                    "rejected",
                    "invalid_required_active_deployment",
                )
            if not self._active_path(candidate).is_file():
                return self._outcome(
                    False,
                    "blocked",
                    "required_recovery_deployment_not_active",
                    required_deployment=candidate,
                )

        try:
            required_memory_mb = int(
                config.get("required_available_memory_mb") or 0
            )
        except (TypeError, ValueError):
            return self._outcome(
                False,
                "rejected",
                "invalid_required_available_memory_mb",
            )
        if required_memory_mb < 0 or required_memory_mb > 262_144:
            return self._outcome(
                False,
                "rejected",
                "invalid_required_available_memory_mb",
            )
        available_memory_mb = self._mem_available_mb()
        if required_memory_mb and available_memory_mb < required_memory_mb:
            return self._outcome(
                False,
                "blocked",
                "insufficient_recovery_memory_headroom",
                required_available_memory_mb=required_memory_mb,
                available_memory_mb=available_memory_mb,
            )

        forbidden = config.get("forbidden_process_tokens") or []
        if not isinstance(forbidden, list):
            return self._outcome(
                False,
                "rejected",
                "invalid_forbidden_process_tokens",
            )
        tokens = []
        for value in forbidden:
            token = str(value).strip().lower()
            if not _SAFE_PROCESS_TOKEN.fullmatch(token):
                return self._outcome(
                    False,
                    "rejected",
                    "invalid_forbidden_process_token",
                )
            tokens.append(token)
        matches: list[dict[str, Any]] = []
        if tokens:
            for process in self._running_processes():
                command = str(process.get("command") or "").lower()
                matched = [token for token in tokens if token in command]
                if matched:
                    matches.append(
                        {
                            "pid": process.get("pid"),
                            "tokens": matched,
                            "command": command[:300],
                        }
                    )
        if matches:
            return self._outcome(
                False,
                "blocked",
                "conflicting_host_process_active",
                deployment=deployment,
                matches=matches[:20],
            )
        return self._outcome(
            True,
            "verified",
            "",
            deployment=deployment,
            available_memory_mb=available_memory_mb,
            required_available_memory_mb=required_memory_mb,
            required_active_deployments=required_active,
            forbidden_process_tokens=tokens,
        )

    @staticmethod
    def _mem_available_mb() -> int:
        try:
            lines = Path("/proc/meminfo").read_text(
                encoding="utf-8"
            ).splitlines()
            for line in lines:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        except (OSError, ValueError, IndexError):
            return 0
        return 0

    @staticmethod
    def _running_processes() -> list[dict[str, Any]]:
        processes = []
        proc = Path("/proc")
        try:
            entries = list(proc.iterdir())
        except OSError:
            return processes
        own_pid = os.getpid()
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) == own_pid:
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            command = raw.replace(b"\x00", b" ").decode(
                "utf-8",
                errors="replace",
            ).strip()
            if command:
                processes.append(
                    {"pid": int(entry.name), "command": command}
                )
        return processes

    def _epoch_path(self, deployment: str) -> Path:
        return self.state_dir / f"{deployment}.activation-epoch.json"
