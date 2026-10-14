from pathlib import Path

from fastapi.testclient import TestClient

from assistx.api import auth
from assistx.api_router import app

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_route_renders_chat_first_surface():
    # Production authentication is intentionally configured via a runtime
    # secret and cannot be exercised with old hardcoded test credentials.
    # Override only the auth dependency for this isolated rendering test.
    app.dependency_overrides[auth] = lambda: "test-operator"
    try:
        with TestClient(app) as client:
            response = client.get("/workbench")
    finally:
        app.dependency_overrides.pop(auth, None)

    assert response.status_code == 200
    assert "Agent Workbench" in response.text
    assert 'id="workbench-messages"' in response.text
    assert 'id="workbench-composer"' in response.text
    assert 'id="workbench-drawer"' in response.text
    assert "/static/js/workbench.js" in response.text
    assert "/static/css/workbench.css" in response.text


def test_workbench_uses_existing_hermes_agent_boundary_only():
    script = (ROOT / "static" / "js" / "workbench.js").read_text(encoding="utf-8")

    assert "/api/v1/agent/chat/completions" in script
    assert "model: 'agent:auto'" in script
    assert "X-Hermes-Session-Id" in script
    assert "X-Hermes-Session-Key" in script
    assert "sessionStorage" in script

    # The workbench must not invent a direct-model bypass around AssistX routing.
    assert "/api/llm/stream" not in script
    assert "/api/v1/model/chat/completions" not in script


def test_workbench_context_drawer_is_read_only():
    script = (ROOT / "static" / "js" / "workbench.js").read_text(encoding="utf-8")

    assert "fetch('/api/sessions?limit=8'" in script
    assert "fetch('/api/dispatches?limit=8'" in script
    assert "shell.onSnapshot(renderSnapshot)" in script

    for mutation_path in (
        "/api/dispatches/",
        "/api/fleet/self-healing/reconcile",
        "/api/fleet/migrations",
        "/preempt",
        "/migrate",
        "/promote",
    ):
        assert mutation_path not in script


def test_workbench_is_responsive_and_reachable_from_shell():
    styles = (ROOT / "static" / "css" / "workbench.css").read_text(encoding="utf-8")
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert ".workbench-layout" in styles
    assert "@media (max-width: 900px)" in styles
    assert "position: fixed" in styles
    assert 'href="/workbench"' in base


def test_workbench_template_keeps_instrumentation_separate_from_chat():
    template = (ROOT / "templates" / "workbench.html").read_text(encoding="utf-8")

    assert "authoritative routing unchanged" in template
    assert "read-only instrumentation" in template
    assert 'data-tab="execution"' in template
    assert 'data-tab="sessions"' in template
    assert 'data-tab="fleet"' in template
