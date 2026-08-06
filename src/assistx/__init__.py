from . import config, llm_client


def _install_runtime_safety_boundaries() -> None:
    # api.py imports ``_start_harvester_loop`` lazily during lifespan startup.
    # Replace only that startup function; the harvester class and focused unit
    # interfaces remain unchanged.
    from . import kg_harvester
    from .durable_harvester import start_durable_harvester_loop
    from .fleet_executor_compat import install_fleet_executor_compatibility
    from .repository_path_policy import install_repository_path_policy
    from .strict_claims import install_strict_claim_fencing
    from .work_supply import install_work_supply_boundaries

    kg_harvester._start_harvester_loop = start_durable_harvester_loop
    install_fleet_executor_compatibility()
    install_repository_path_policy()
    install_strict_claim_fencing()
    install_work_supply_boundaries()


_install_runtime_safety_boundaries()

__all__ = ["__version__", "llm_client", "config"]
__version__ = "0.1.0"
