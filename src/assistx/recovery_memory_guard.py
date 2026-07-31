from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SAFE_UNIT = re.compile(r"^[A-Za-z0-9_.@-]{1,128}\.service$")
_PROTECTED_MARKERS = {
    "assistx-recovery-island",
    "assistx-degraded",
    "falkordb",
    "redis",
    "tailscale",
    "network",
    "docker",
    "containerd",
    "ssh",
}


def read_meminfo(path: str | Path = "/proc/meminfo") -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        number = raw.strip().split()[0]
        try:
            values[key] = int(number) // 1024
        except ValueError:
            continue
    if "MemTotal" not in values or "MemAvailable" not in values:
        raise ValueError("MemTotal and MemAvailable are required")
    return {
        "total_mb": values["MemTotal"],
        "available_mb": values["MemAvailable"],
        "swap_free_mb": values.get("SwapFree", 0),
    }


def pressure_level(available_mb: int) -> tuple[str, list[str]]:
    available = max(0, int(available_mb))
    if available >= 2048:
        return "NORMAL", []
    if available >= 1536:
        return "ELEVATED", ["trim_optional_caches"]
    if available >= 1024:
        return "CRITICAL", ["trim_optional_caches", "stop_local_model"]
    return "EMERGENCY", [
        "trim_optional_caches",
        "stop_local_model",
        "block_neo4j_promotion",
        "reject_new_work",
    ]


def validate_sheddable_units(values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        unit = str(raw or "").strip()
        if not unit:
            continue
        lowered = unit.lower()
        if not _SAFE_UNIT.fullmatch(unit):
            raise ValueError(f"invalid systemd unit name: {unit}")
        if any(marker in lowered for marker in _PROTECTED_MARKERS):
            raise ValueError(f"protected systemd unit cannot be shed: {unit}")
        if unit not in result:
            result.append(unit)
    return result


class RecoveryMemoryGuard:
    def __init__(
        self,
        *,
        state_dir: str | Path,
        sheddable_units: list[str],
        runner: Callable[..., Any] = subprocess.run,
        meminfo_path: str | Path = "/proc/meminfo",
        user_services: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.units = validate_sheddable_units(sheddable_units)
        self.runner = runner
        self.meminfo_path = Path(meminfo_path)
        self.user_services = user_services
        self.clock = clock

    def evaluate(self) -> dict[str, Any]:
        memory = read_meminfo(self.meminfo_path)
        level, actions = pressure_level(memory["available_mb"])
        previous = self._read_state()
        result: dict[str, Any] = {
            "ok": True,
            "level": level,
            "memory": memory,
            "planned_actions": actions,
            "executed_actions": [],
            "failed_actions": [],
            "observed_at": int(self.clock()),
        }
        if "stop_local_model" in actions:
            for unit in self.units:
                if unit in set(previous.get("stopped_units") or []):
                    continue
                command = ["systemctl"]
                if self.user_services:
                    command.append("--user")
                command.extend(["stop", unit])
                process = self.runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if process.returncode == 0:
                    result["executed_actions"].append(
                        {"action": "stop_local_model", "unit": unit}
                    )
                else:
                    result["failed_actions"].append(
                        {
                            "action": "stop_local_model",
                            "unit": unit,
                            "stderr": str(getattr(process, "stderr", ""))[-500:],
                        }
                    )
        stopped = sorted(
            set(previous.get("stopped_units") or [])
            | {
                item["unit"]
                for item in result["executed_actions"]
                if item.get("unit")
            }
        )
        result["stopped_units"] = stopped
        self._set_flag("block-neo4j-promotion", "block_neo4j_promotion" in actions)
        self._set_flag("reject-new-work", "reject_new_work" in actions)
        result["ok"] = not result["failed_actions"]
        self._write_state(result)
        return result

    def _set_flag(self, name: str, enabled: bool) -> None:
        path = self.state_dir / name
        if enabled:
            path.write_text(str(int(self.clock())) + "\n", encoding="utf-8")
            path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)

    def _read_state(self) -> dict[str, Any]:
        path = self.state_dir / "memory-guard.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, value: dict[str, Any]) -> None:
        path = self.state_dir / "memory-guard.json"
        descriptor, temporary = tempfile.mkstemp(
            prefix=path.name + ".",
            dir=str(self.state_dir),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
