from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryReservation:
    name: str
    megabytes: int
    required: bool = True
    notes: str = ""


DEFAULT_TOTAL_MB = 14 * 1024
DEFAULT_SAFETY_RESERVE_MB = 1800


def _positive(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def memory_plan(
    *,
    mode: str,
    total_mb: int = DEFAULT_TOTAL_MB,
    headless_llm_mb: int = 5500,
    neo4j_heap_mb: int = 1024,
    neo4j_pagecache_mb: int = 512,
    restore_workspace_mb: int = 1536,
    safety_reserve_mb: int = DEFAULT_SAFETY_RESERVE_MB,
) -> dict[str, Any]:
    normalized = str(mode or "standby").lower()
    if normalized not in {"standby", "continuity", "durable", "executor"}:
        raise ValueError(f"unsupported continuity memory mode: {mode}")

    reservations = [
        MemoryReservation("host-os-and-docker", 2200, notes="kernel, container runtime, Tailscale, filesystem cache"),
        MemoryReservation("falkordb-hot-state", 1024, notes="768 MiB maxmemory plus module/process overhead"),
        MemoryReservation("redis-queue", 256, notes="bounded RQ/stream queue with no eviction"),
        MemoryReservation("continuity-api", 384),
        MemoryReservation("auto-router", 320),
        MemoryReservation("recovery-agent-and-monitoring", 192),
    ]

    if normalized in {"standby", "continuity", "executor"}:
        reservations.append(
            MemoryReservation(
                "headless-small-llm",
                max(0, int(headless_llm_mb)),
                required=normalized != "durable",
                notes="LM Studio or compatible external runtime; model-dependent",
            )
        )
    if normalized in {"durable", "executor"}:
        reservations.extend(
            [
                MemoryReservation("neo4j-heap", max(512, int(neo4j_heap_mb))),
                MemoryReservation("neo4j-pagecache", max(256, int(neo4j_pagecache_mb))),
                MemoryReservation("neo4j-native-overhead", 768),
                MemoryReservation("neo4j-restore-workspace", max(512, int(restore_workspace_mb))),
                MemoryReservation("continuity-reconciler", 256),
            ]
        )

    required_mb = sum(item.megabytes for item in reservations if item.required)
    projected_mb = sum(item.megabytes for item in reservations)
    safety = max(512, int(safety_reserve_mb))
    available_after_required = int(total_mb) - required_mb
    available_after_projected = int(total_mb) - projected_mb
    pass_required = available_after_required >= safety
    pass_projected = available_after_projected >= safety

    required_actions: list[str] = []
    if normalized == "durable" and headless_llm_mb > 0:
        required_actions.append("drain or stop the headless LLM before Neo4j restore/commit activation")
    if not pass_projected:
        required_actions.append("reduce service memory caps or stop optional services before activation")
    if normalized == "executor":
        required_actions.append("keep Hermes disabled until one fenced synthetic task succeeds")

    return {
        "mode": normalized,
        "total_mb": int(total_mb),
        "safety_reserve_mb": safety,
        "required_mb": required_mb,
        "projected_mb": projected_mb,
        "available_after_required_mb": available_after_required,
        "available_after_projected_mb": available_after_projected,
        "required_fit": pass_required,
        "projected_fit": pass_projected,
        "reservations": [asdict(item) for item in reservations],
        "required_actions": required_actions,
    }


def plan_from_env(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    return memory_plan(
        mode=source.get("ASSISTX_CONTINUITY_MEMORY_MODE", "standby"),
        total_mb=_positive(source.get("ASSISTX_CONTINUITY_TOTAL_MB"), DEFAULT_TOTAL_MB),
        headless_llm_mb=_positive(source.get("ASSISTX_HEADLESS_LLM_RESERVED_MB"), 5500),
        neo4j_heap_mb=_positive(source.get("ASSISTX_NEO4J_HEAP_MB"), 1024),
        neo4j_pagecache_mb=_positive(source.get("ASSISTX_NEO4J_PAGECACHE_MB"), 512),
        restore_workspace_mb=_positive(source.get("ASSISTX_NEO4J_RESTORE_WORKSPACE_MB"), 1536),
        safety_reserve_mb=_positive(source.get("ASSISTX_CONTINUITY_SAFETY_RESERVE_MB"), DEFAULT_SAFETY_RESERVE_MB),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Beelink continuity memory envelope")
    parser.add_argument("--mode", choices=["standby", "continuity", "durable", "executor"])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    env = dict(os.environ)
    if args.mode:
        env["ASSISTX_CONTINUITY_MEMORY_MODE"] = args.mode
    plan = plan_from_env(env)
    if args.as_json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(
            f"mode={plan['mode']} required={plan['required_mb']}MiB "
            f"projected={plan['projected_mb']}MiB total={plan['total_mb']}MiB "
            f"reserve={plan['safety_reserve_mb']}MiB fit={plan['projected_fit']}"
        )
        for action in plan["required_actions"]:
            print(f"ACTION: {action}")
    return 0 if plan["required_fit"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
