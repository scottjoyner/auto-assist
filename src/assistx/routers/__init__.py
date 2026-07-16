"""Feature routers for the AssistX API (W-18).

Each module returns an ``APIRouter`` built from the shared helpers in
``assistx.api`` (imported lazily inside route functions to avoid a circular
import at module load). The routers are registered in ``api_router.py``.

This is an incremental extraction of the former 3739-LOC ``api.py`` monolith;
route behavior is preserved byte-for-byte where possible.
"""

from .devices import build_devices_router
from .feeds import build_feeds_router
from .review import build_review_router
from .intents import build_intents_router
from .memory import build_memory_router
from .dispatch import build_dispatch_router

__all__ = [
    "build_devices_router",
    "build_feeds_router",
    "build_review_router",
    "build_intents_router",
    "build_tickets_router",
    "build_memory_router",
    "build_transcriptions_router",
    "build_dispatch_router",
]
