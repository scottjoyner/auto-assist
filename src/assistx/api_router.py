from __future__ import annotations

from .api import app, _neo, auth
from .passive_agents import build_passive_agent_router
from .passive_claims import build_passive_claim_router
from .passive_control import build_passive_control_router
from .passive_events import build_passive_event_router
from .passive_status import build_passive_status_router
from .router_integration import build_router_integration_router


app.include_router(build_router_integration_router(_neo))
app.include_router(build_passive_agent_router(_neo, auth_dependency=auth))
app.include_router(build_passive_claim_router(_neo, auth_dependency=auth))
app.include_router(build_passive_control_router(_neo, auth_dependency=auth))
app.include_router(build_passive_status_router(_neo, auth_dependency=auth))
app.include_router(build_passive_event_router(_neo, auth_dependency=auth))
