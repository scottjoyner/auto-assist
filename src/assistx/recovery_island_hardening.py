from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .recovery_island import RecoveryIslandExecutor, verify_recovery_activation


class HardenedRecoveryIslandExecutor(RecoveryIslandExecutor):
    """Production durability checks layered over the bounded base executor."""

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
            image_id = str(image.get("id") or "") if isinstance(image, dict) else ""
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
            return self._outcome(False, "blocked", "recovery_island_not_prepared")
        activation = parameters.get("activation")
        if not isinstance(activation, dict):
            return self._outcome(False, "blocked", "missing_recovery_activation")
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

        # Persist the highest accepted epoch before starting containers. A failed
        # or rolled-back activation still consumes its epoch and cannot be replayed.
        epoch_record = {
            "deployment": deployment,
            "node_id": self.node_id,
            "epoch": int(activation["epoch"]),
            "bundle_sha256": str(activation.get("bundle_sha256") or ""),
            "fence_proof": str(activation.get("fence_proof") or ""),
        }
        self._write_json(self._epoch_path(deployment), epoch_record)
        return super()._activate(deployment, config, parameters)

    def _epoch_path(self, deployment: str) -> Path:
        return self.state_dir / f"{deployment}.activation-epoch.json"
