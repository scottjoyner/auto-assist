"""Feature routers for the AssistX API (W-18).

Each module returns an ``APIRouter`` built from the shared helpers in
``assistx.api`` (imported lazily inside route functions to avoid a circular
import at module load). The routers are registered in ``api_router.py``.

This is an incremental extraction of the former 3739-LOC ``api.py`` monolith;
route behavior is preserved byte-for-byte where possible.
"""

from .devices import build_devices_router

__all__ = ["build_devices_router"]
