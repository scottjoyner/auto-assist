"""Transcriptions router (W-18 extraction from api.py).

Routes:
  GET  /api/transcriptions
  GET  /api/transcriptions/{tid}
  POST /api/transcriptions/{tid}/task
  POST /api/transcriptions/{tid}/embed
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from pydantic import BaseModel, Field

from ..api import auth, _neo


class TranscriptionTaskIn(BaseModel):
    title: str
    status: str = "pending"
    kind: str = "transcription"
    payload: dict = Field(default_factory=dict)


router = APIRouter(tags=["transcriptions"])


@router.get("/api/transcriptions")
def api_list_transcriptions(
    q: Optional[str] = Query(None, description="text contains (case-insensitive)"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo._session() as s:
            if q:
                res = s.run(
                    """
                    MATCH (tr:Transcription)
                    WHERE toLower(tr.text) CONTAINS toLower($q)
                    RETURN tr
                    ORDER BY coalesce(tr.created_at_ts,0) DESC
                    LIMIT $limit
                    """,
                    {"q": q, "limit": limit},
                )
            else:
                res = s.run(
                    """
                    MATCH (tr:Transcription)
                    RETURN tr
                    ORDER BY coalesce(tr.created_at_ts,0) DESC
                    LIMIT $limit
                    """,
                    {"limit": limit},
                )
            items = [dict(r["tr"]) for r in res]
            return {"items": items, "count": len(items)}
    finally:
        neo.close()


@router.get("/api/transcriptions/{tid}")
def api_get_transcription(tid: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            rec = s.run(
                """
                MATCH (tr:Transcription {id:$id})
                OPTIONAL MATCH (tr)<-[:ABOUT]-(t:Task)
                RETURN tr, collect(t) AS tasks
                """,
                {"id": tid},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Transcription not found")
            tr = dict(rec["tr"])
            tasks = [dict(t) for t in rec["tasks"] if t]
            return {"transcription": tr, "tasks": tasks}
    finally:
        neo.close()


@router.post("/api/transcriptions/{tid}/task")
def api_create_task_from_transcription(tid: str, body: TranscriptionTaskIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            has = s.run("MATCH (tr:Transcription {id:$id}) RETURN tr", {"id": tid}).single()
            if not has:
                raise HTTPException(status_code=404, detail="Transcription not found")

            task_id = uuid.uuid4().hex
            res = s.run(
                """
                CREATE (t:Task {id:$task_id})
                SET t += $props,
                    t.created_at = datetime(), t.created_at_ts = timestamp()
                WITH t
                MATCH (tr:Transcription {id:$tid})
                MERGE (t)-[:ABOUT]->(tr)
                RETURN t.id AS id
                """,
                {
                    "task_id": task_id,
                    "props": {
                        "title": body.title,
                        "status": body.status,
                        "kind": body.kind,
                        "payload_json": json.dumps(body.payload or {}),
                        "transcription_id": tid,
                    },
                    "tid": tid,
                },
            ).single()
            return {"task_id": res["id"]}
    finally:
        neo.close()


@router.post("/api/transcriptions/{tid}/embed")
def api_embed_transcription(tid: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            rec = s.run("MATCH (tr:Transcription {id:$id}) RETURN tr", {"id": tid}).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Transcription not found")

            task_id = uuid.uuid4().hex
            res = s.run(
                """
                CREATE (t:Task {id:$task_id})
                SET t.title='Embed transcription',
                    t.status='READY',
                    t.kind='embed_transcription',
                    t.transcription_id=$tid,
                    t.created_at=datetime(), t.created_at_ts=timestamp()
                WITH t
                MATCH (tr:Transcription {id:$tid})
                MERGE (t)-[:ABOUT]->(tr)
                RETURN t.id AS id
                """,
                {"task_id": task_id, "tid": tid},
            ).single()
            return {"task_id": res["id"], "status": "READY"}
    finally:
        neo.close()


def build_transcriptions_router() -> APIRouter:
    return router
