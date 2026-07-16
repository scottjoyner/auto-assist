from __future__ import annotations

from .api import app, _neo, auth
from .passive_agents import build_passive_agent_router
from .passive_claims import build_passive_claim_router
from .passive_control import build_passive_control_router
from .passive_events import build_passive_event_router
from .passive_status import build_passive_status_router
from .router_integration import build_router_integration_router
from .overlay_routes import build_overlay_router
from .routers.devices import build_devices_router
from .routers.feeds import build_feeds_router
from .routers.review import build_review_router
from .routers.intents import build_intents_router
from .routers.tickets import build_tickets_router
from .routers.memory import build_memory_router
from .routers.transcriptions import build_transcriptions_router
from .routers.dispatch import build_dispatch_router


app.include_router(build_router_integration_router(_neo))
app.include_router(build_overlay_router())
app.include_router(build_passive_agent_router(_neo, auth_dependency=auth))
app.include_router(build_passive_claim_router(_neo, auth_dependency=auth))
app.include_router(build_passive_control_router(_neo, auth_dependency=auth))
app.include_router(build_passive_status_router(_neo, auth_dependency=auth))
app.include_router(build_passive_event_router(_neo, auth_dependency=auth))
app.include_router(build_devices_router())
app.include_router(build_feeds_router())
app.include_router(build_review_router())
app.include_router(build_intents_router())
app.include_router(build_tickets_router())
app.include_router(build_memory_router())
app.include_router(build_transcriptions_router())
app.include_router(build_dispatch_router())
