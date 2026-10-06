"""Minimal entrypoint that installs scoped executor authentication before polling."""

from __future__ import annotations

from assistx.agents import hermes_agent_adapter
from assistx.strict_executor_adapter import install_strict_executor_adapter


def main() -> None:
    install_strict_executor_adapter(hermes_agent_adapter)
    hermes_agent_adapter.run_loop()


if __name__ == "__main__":
    main()
