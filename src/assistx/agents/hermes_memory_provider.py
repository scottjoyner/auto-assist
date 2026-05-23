from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests


class HermesMemoryProvider:
    def __init__(
        self,
        base_url: Optional[str] = None,
        auth: Optional[tuple[str, str]] = None,
        api_token: Optional[str] = None,
    ):
        self.base_url = base_url or os.getenv("ASSISTX_API_URL", "http://localhost:8000")
        self.auth = auth
        self.headers: Dict[str, str] = {}
        token = api_token or os.getenv("API_TOKEN")
        if token:
            self.headers["x-api-token"] = token

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = requests.post(url, json=payload, headers=self.headers, auth=self.auth, timeout=30)
        resp.raise_for_status()
        return resp.json()

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
    ) -> str:
        body = {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "session_id": session_id,
            "paperclip_issue_id": paperclip_issue_id,
            "paperclip_run_id": paperclip_run_id,
        }
        data = self._post("/api/brain/signals", body)
        return data["signal_event_id"]

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
