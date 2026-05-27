from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import requests


class HermesMemoryProvider:
    def __init__(
        self,
        base_url: Optional[str] = None,
        auth: Optional[tuple[str, str]] = None,
        api_token: Optional[str] = None,
        outbox: Any = None,
    ):
        self.base_url = base_url or os.getenv("ASSISTX_API_URL", "http://localhost:8000")
        self.auth = auth
        self.headers: Dict[str, str] = {}
        token = api_token or os.getenv("API_TOKEN")
        if token:
            self.headers["x-api-token"] = token
        self._outbox = outbox

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = requests.post(url, json=payload, headers=self.headers, auth=self.auth, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _fallback_to_outbox(self, path: str, payload: Dict[str, Any]) -> None:
        if self._outbox is None:
            return
        from ..outbox_client import OutboxClient
        outbox = self._outbox if isinstance(self._outbox, OutboxClient) else OutboxClient()
        outbox.enqueue({
            "event_id": str(uuid.uuid4()),
            "event_type": "hermes.signal",
            "source_repo": "hermes-agent",
            "source_service": "memory-provider",
            "node_id": os.uname().nodename,
            "occurred_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "idempotency_key": f"hermes-{payload.get('event_id', payload.get('session_id', str(uuid.uuid4())))}",
            "schema_version": "1.0",
            "subject": {"kind": "signal", "id": payload.get("event_id", "unknown")},
            "payload": {"path": path, "body": payload},
            "artifact_refs": [],
            "privacy": {"pii": False, "privacy_class": "private", "retention_class": "keep"},
        })

    def prefetch(
        self,
        query: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        max_items: int = 20,
        include_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "query": query,
            "task_id": task_id,
            "session_id": session_id,
            "max_items": max_items,
            "include_sources": include_sources or ["memory", "knowledge", "orchestration"],
        }
        data = self._post("/api/brain/context", payload)
        return data["context_packet"]

    def system_prompt_block(self, session_id: Optional[str] = None) -> str:
        return (
            "You have access to shared graph memory through the AssistX/Neo4j brain. "
            "Before acting on a task, call prefetch or graph_context_search to load "
            "relevant context. After completing work, use write_memory to persist "
            "durable facts, observations, and outcomes. Use signal_event to record "
            "lifecycle events. Your memory writes are visible to other agents and sessions."
        )

    def sync_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        task_id: Optional[str] = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        payload = {
            "user_text": user_text,
            "assistant_text": assistant_text,
        }
        event_type = "turn_sync"
        return self.signal_event(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            session_id=session_id,
        )

    def on_delegation(
        self,
        session_id: str,
        child_task_id: str,
        child_result: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        payload = {
            "child_task_id": child_task_id,
            "child_result": child_result or {},
        }
        event_type = "delegation"
        return self.signal_event(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            session_id=session_id,
        )

    def on_session_switch(
        self,
        session_id: str,
        previous_session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> str:
        if previous_session_id:
            self.update_session(
                session_id=session_id,
                metadata={"previous_session_id": previous_session_id, "switch_reason": reason or "unknown"},
            )
        event_id = uuid.uuid4().hex
        payload = {
            "previous_session_id": previous_session_id,
            "reason": reason or "unknown",
        }
        event_type = "session_switch"
        return self.signal_event(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            session_id=session_id,
        )

    def write_memory(
        self,
        kind: str,
        text: str,
        source: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload = {
            "kind": kind,
            "text": text,
            "source": source,
            "session_id": session_id,
            "task_id": task_id,
            "metadata": metadata or {},
        }
        data = self._post("/api/memory/items", payload)
        return data["memory_item_id"]

    def signal_event(
        self,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        paperclip_issue_id: Optional[str] = None,
        paperclip_run_id: Optional[str] = None,
    ) -> Optional[str]:
        body = {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "session_id": session_id,
            "paperclip_issue_id": paperclip_issue_id,
            "paperclip_run_id": paperclip_run_id,
        }
        try:
            data = self._post("/api/brain/signals", body)
            return data["signal_event_id"]
        except requests.RequestException:
            self._fallback_to_outbox("/api/brain/signals", body)
            return None

    def update_session(
        self,
        session_id: str,
        paperclip_agent_id: Optional[str] = None,
        hermes_session_id: Optional[str] = None,
        agent_identity: Optional[str] = None,
        device_id: Optional[str] = None,
        platform: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        body = {
            "paperclip_agent_id": paperclip_agent_id,
            "hermes_session_id": hermes_session_id,
            "agent_identity": agent_identity,
            "device_id": device_id,
            "platform": platform,
            "metadata": metadata or {},
        }
        data = self._post(f"/api/sessions/{session_id}", body)
        return data["session_id"]
