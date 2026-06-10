"""Basic integration health tests — all 3 services must be running."""

from __future__ import annotations

import httpx


def test_assistx_health(assistx_client: httpx.Client) -> None:
    resp = assistx_client.get("/health")
    assert resp.is_success, f"AssistX health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("service") == "assistx"
    assert data.get("ok") is not False


def test_router_health(router_client: httpx.Client) -> None:
    resp = router_client.get("/health")
    assert resp.is_success, f"Router health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("service") == "auto-router"


def test_assign_health(assign_client: httpx.Client) -> None:
    resp = assign_client.get("/health")
    assert resp.is_success, f"Assign health failed: {resp.status_code}"
    data = resp.json()
    assert data.get("status") in ("ok", "degraded")


def test_cross_service_health(assistx_client: httpx.Client) -> None:
    """AssistX health response includes deps status for router and other backends."""
    resp = assistx_client.get("/health")
    data = resp.json()
    deps = data.get("deps", {})
    for dep in ("redis",):
        assert dep in deps, f"Missing dep '{dep}' in AssistX health response"


def test_correlation_id_propagation(assistx_client: httpx.Client) -> None:
    """Correlation ID header is echoed back."""
    cid = "test-cid-integration"
    resp = assistx_client.get("/health", headers={"X-Correlation-ID": cid})
    assert resp.headers.get("X-Correlation-ID") == cid


def test_trace_id_propagation(assistx_client: httpx.Client) -> None:
    """Trace ID header is echoed back."""
    tid = "test-tid-integration"
    resp = assistx_client.get("/health", headers={"X-Trace-ID": tid})
    assert resp.headers.get("X-Trace-ID") == tid
