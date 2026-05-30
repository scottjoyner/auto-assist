from __future__ import annotations

from .api import app, _neo
from .router_integration import build_router_integration_router


app.include_router(build_router_integration_router(_neo))
