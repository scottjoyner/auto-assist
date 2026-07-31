from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI

from assistx import recovery_mode


class FakeNeo:
    instances = []

    def __init__(self):
        self.schema_ensured = False
        self.closed = False
        self.__class__.instances.append(self)

    def ensure_schema(self):
        self.schema_ensured = True

    def close(self):
        self.closed = True


def test_recovery_shadow_replaces_normal_lifespan(monkeypatch):
    monkeypatch.setenv("ASSISTX_RECOVERY_SHADOW_MODE", "true")
    app = FastAPI()
    normal_started = {"value": False}

    async def normal_lifespan(_app):
        normal_started["value"] = True
        yield

    app.router.lifespan_context = normal_lifespan
    module = SimpleNamespace(
        app=app,
        Neo4jClient=FakeNeo,
        validate_runtime_configuration=lambda strict=False: {"ok": strict},
    )

    status = recovery_mode.install_recovery_shadow_mode(module)

    async def exercise():
        async with app.router.lifespan_context(app):
            assert status["enabled"] is True
            assert status["mode"] == "recovery_shadow"

    asyncio.run(exercise())
    assert normal_started["value"] is False
    assert FakeNeo.instances[-1].schema_ensured is True
    assert FakeNeo.instances[-1].closed is True
    assert recovery_mode.recovery_shadow_status()["execution_promoted"] is False


def test_normal_mode_leaves_lifespan_unchanged(monkeypatch):
    monkeypatch.delenv("ASSISTX_RECOVERY_SHADOW_MODE", raising=False)
    app = FastAPI()
    original = app.router.lifespan_context
    module = SimpleNamespace(app=app)

    status = recovery_mode.install_recovery_shadow_mode(module)

    assert status["enabled"] is False
    assert app.router.lifespan_context is original
