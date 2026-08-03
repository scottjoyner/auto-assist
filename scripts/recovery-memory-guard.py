#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

from assistx.recovery_memory_guard import RecoveryMemoryGuard


def main() -> int:
    units = [
        value.strip()
        for value in os.getenv("RECOVERY_SHEDDABLE_SYSTEMD_UNITS", "").split(",")
        if value.strip()
    ]
    try:
        guard = RecoveryMemoryGuard(
            state_dir=os.getenv(
                "FLEET_RECOVERY_ISLAND_STATE_DIR",
                "/var/lib/assistx-recovery/state",
            ),
            sheddable_units=units,
            meminfo_path=os.getenv("RECOVERY_MEMINFO_PATH", "/proc/meminfo"),
            user_services=os.getenv(
                "RECOVERY_SHEDDABLE_UNITS_ARE_USER_SERVICES",
                "true",
            ).strip().lower()
            in {"1", "true", "yes", "on"},
        )
        result = guard.evaluate()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
