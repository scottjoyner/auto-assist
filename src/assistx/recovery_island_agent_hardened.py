from __future__ import annotations

import argparse

from . import recovery_island_agent as base_agent
from .recovery_island_hardening import HardenedRecoveryIslandExecutor

_BASE_EXECUTOR_FACTORY = base_agent._executor


def _hardened_executor(
    args: argparse.Namespace,
) -> HardenedRecoveryIslandExecutor:
    configured = _BASE_EXECUTOR_FACTORY(args)
    return HardenedRecoveryIslandExecutor(
        node_id=configured.node_id,
        state_dir=str(configured.state_dir),
        http=configured.http,
        runner=configured.runner,
        env=configured.env,
        runbook_keys=configured.runbook_keys,
        activation_keys=configured.activation_keys,
    )


def main() -> None:
    # Reuse the thoroughly bounded CLI/polling implementation while replacing
    # only its executor factory with the production durability layer.
    base_agent._executor = _hardened_executor
    base_agent.main()


if __name__ == "__main__":
    main()
