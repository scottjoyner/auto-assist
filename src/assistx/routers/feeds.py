"""Feeds + evaluations router (W-18 extraction from api.py).

Routes:
  GET  /api/feeds
  POST /api/feeds
  GET  /api/evaluations
  GET  /api/evaluations/suites
  POST /api/evaluations/suites
  POST /api/evaluations
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api import (
    EvaluationRunIn,
    EvaluationSuiteUpsertIn,
    FeedConnectorUpsertIn,
    auth,
    _neo,
)
from .. import evaluation_registry as _eval_registry
from .. import feed_registry as _feed_registry

router = APIRouter(tags=["feeds", "evaluations"])


@router.get("/api/feeds")
def api_list_feeds(
    limit: int = Query(200, ge=1, le=1000),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        summary = _feed_registry.feed_health_summary()
        for c in summary.get("connectors", []):
            neo.upsert_data_feed_connector(
                connector_id=str(c.get("id")),
                name=str(c.get("name")),
                category=str(c.get("category") or "general"),
                endpoint=str(c.get("endpoint") or ""),
                enabled=bool(c.get("enabled")),
                health_status=str(c.get("health_status") or "degraded"),
                metadata={"source": "registry_sync", "updated_at_ts": c.get("updated_at_ts")},
            )
        items = neo.list_data_feed_connectors(limit=limit)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()


@router.post("/api/feeds")
def api_upsert_feed(body: FeedConnectorUpsertIn, user: str = Depends(auth)):
    if body.health_status not in {"healthy", "degraded", "down"}:
        raise HTTPException(status_code=400, detail="health_status must be healthy, degraded, or down")
    neo = _neo()
    try:
        connector_id = neo.upsert_data_feed_connector(
            connector_id=body.id,
            name=body.name,
            category=body.category,
            endpoint=body.endpoint,
            enabled=body.enabled,
            health_status=body.health_status,
            metadata=body.metadata,
        )
        return {"id": connector_id, "ok": True}
    finally:
        neo.close()


@router.get("/api/evaluations")
def api_list_evaluations(
    status: Optional[str] = Query(None, description="Evaluation run status filter"),
    limit: int = Query(100, ge=1, le=1000),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_evaluation_runs(limit=limit, status=status)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()


@router.get("/api/evaluations/suites")
def api_list_evaluation_suites(
    enabled: Optional[bool] = Query(None, description="Filter suites by enabled flag"),
    limit: int = Query(200, ge=1, le=1000),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        summary = _eval_registry.suites_summary()
        for suite in summary.get("suites", []):
            neo.upsert_evaluation_suite(
                name=str(suite.get("name")),
                agent_class=str(suite.get("agent_class")),
                enabled=bool(suite.get("enabled")),
                cadence=str(suite.get("cadence") or "daily"),
                threshold=float(suite.get("threshold") or 0.8),
                description=str(suite.get("description") or ""),
                metadata={"source": "registry_sync"},
            )
        items = neo.list_evaluation_suites(limit=limit, enabled=enabled)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()


@router.post("/api/evaluations/suites")
def api_upsert_evaluation_suite(body: EvaluationSuiteUpsertIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        suite_id = neo.upsert_evaluation_suite(
            name=body.name,
            agent_class=body.agent_class,
            enabled=body.enabled,
            cadence=body.cadence,
            threshold=body.threshold,
            description=body.description,
            metadata={**(body.metadata or {}), "updated_by": user},
        )
        return {"suite_id": suite_id, "ok": True}
    finally:
        neo.close()


@router.post("/api/evaluations")
def api_create_evaluation(body: EvaluationRunIn, user: str = Depends(auth)):
    allowed = {"queued", "running", "completed", "failed", "cancelled"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(allowed))}")
    neo = _neo()
    try:
        run_id = neo.create_evaluation_run(
            suite_name=body.suite_name,
            agent_class=body.agent_class,
            status=body.status,
            score=body.score,
            metadata={**(body.metadata or {}), "recorded_by": user},
        )
        return {"evaluation_run_id": run_id}
    finally:
        neo.close()


def build_feeds_router() -> APIRouter:
    return router
