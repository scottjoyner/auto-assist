from __future__ import annotations

from .api import app, _neo, auth
from .passive_agents import build_passive_agent_router
from .router_integration import build_router_integration_router


app.include_router(build_router_integration_router(_neo))
app.include_router(build_passive_agent_router(_neo, auth_dependency=auth))
