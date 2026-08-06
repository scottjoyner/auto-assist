from . import config, llm_client


def _install_runtime_safety_boundaries() -> None:
    # api.py imports ``_start_harvester_loop`` lazily during lifespan startup.
    # Replace only that startup function; the harvester class and focused unit
    # interfaces remain unchanged.
    from . import kg_harvester
    from .durable_harvester import start_durable_harvester_loop
    from .strict_claims import install_strict_claim_fencing

    kg_harvester._start_harvester_loop = start_durable_harvester_loop
    install_strict_claim_fencing()


_install_runtime_safety_boundaries()

__all__ = ["__version__", "llm_client", "config"]
__version__ = "0.1.0"
