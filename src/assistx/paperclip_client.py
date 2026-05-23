"""
Paperclip API client for AssistX.

Handles creation of issues, agent management, and event polling from Paperclip.
"""

from __future__ import annotations

import os
import requests
import time
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class PaperclipClient:
    """
    Client for interacting with the Paperclip API.

    Paperclip is the canonical assignment hub for cross-device agent work.
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_token: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        self.api_url = api_url or os.getenv("PAPERCLIP_API_URL")
        self.api_token = api_token or os.getenv("PAPERCLIP_API_TOKEN")
        self.workspace_id = workspace_id or os.getenv("PAPERCLIP_WORKSPACE_ID")

        if not (self.api_url and self.api_token and self.workspace_id):
            raise ValueError(
                "Paperclip requires PAPERCLIP_API_URL, PAPERCLIP_API_TOKEN, "
                "and PAPERCLIP_WORKSPACE_ID environment variables to be set."
            )

        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        self.timeout = 30

    def _request(
        self, method: str, path: str, **kwargs
    ) -> Dict[str, Any]:
        """Make a request to Paperclip API."""
        url = f"{self.api_url}{path}"
        kwargs.setdefault("headers", {}).update(self.headers)
        kwargs.setdefault("timeout", self.timeout)

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = requests.request(method, url, **kwargs)
                if resp.status_code >= 500 and attempt < 2:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                last_exc = e
                if attempt >= 2:
                    raise
                time.sleep(0.25 * (2 ** attempt))
        if last_exc:
            raise last_exc
        raise RuntimeError("Paperclip request failed without response")

    def create_issue(
        self,
        title: str,
        description: str,
        task_id: str,
        context_packet_id: str,
        capabilities: List[str],
        priority: str = "normal",
        assignee_id: Optional[str] = None,
    ) -> str:
        """
        Create a Paperclip issue from an AssistX task.

        Args:
            title: Issue title
            description: Issue description
            task_id: AssistX task ID
            context_packet_id: AssistX context packet ID
            capabilities: Required agent capabilities
            priority: Issue priority (low, normal, high, urgent)
            assignee_id: Optional Paperclip agent ID

        Returns:
            Paperclip issue ID
        """
        payload = {
            "workspace_id": self.workspace_id,
            "title": title,
            "description": description,
            "assignee_id": assignee_id,
            "priority": priority,
            "metadata": {
                "assistx_task_id": task_id,
                "assistx_context_packet_id": context_packet_id,
                "required_capabilities": capabilities,
                "source": "assistx-migration",
            },
        }
        result = self._request("POST", "/issues", json=payload)
        return result["id"]

    def get_issue(self, issue_id: str) -> Dict[str, Any]:
        """Fetch issue details."""
        return self._request("GET", f"/issues/{issue_id}")

    def update_issue(self, issue_id: str, **kwargs) -> Dict[str, Any]:
        """Update issue fields."""
        return self._request("PATCH", f"/issues/{issue_id}", json=kwargs)

    def list_issues(
        self,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List issues with optional filters."""
        params = {
            "workspace_id": self.workspace_id,
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status
        if agent_id:
            params["agent_id"] = agent_id

        result = self._request("GET", "/issues", params=params)
        return result.get("issues", [])

    def assign_issue(self, issue_id: str, agent_id: str) -> bool:
        """Assign issue to an agent."""
        payload = {"agent_id": agent_id}
        result = self._request("POST", f"/issues/{issue_id}/assign", json=payload)
        return result.get("success", False)

    def list_agents(
        self, workspace_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List registered agents in workspace.

        Returns list of agent dicts with id, name, capabilities, status, etc.
        """
        params = {"workspace_id": workspace_id or self.workspace_id}
        result = self._request("GET", "/agents", params=params)
        return result.get("agents", [])

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """Get agent details."""
        return self._request("GET", f"/agents/{agent_id}")

    def list_runs(
        self,
        issue_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List runs (executions) with optional filters."""
        params = {
            "workspace_id": self.workspace_id,
            "limit": limit,
        }
        if issue_id:
            params["issue_id"] = issue_id
        if agent_id:
            params["agent_id"] = agent_id

        result = self._request("GET", "/runs", params=params)
        return result.get("runs", [])

    def get_run(self, run_id: str) -> Dict[str, Any]:
        """Get run details."""
        return self._request("GET", f"/runs/{run_id}")

    def get_run_output(self, run_id: str) -> str:
        """Get run output/logs."""
        result = self._request("GET", f"/runs/{run_id}/output")
        return result.get("output", "")

    def poll_events(
        self,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
        since_timestamp: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Poll for events from Paperclip.

        Useful if webhook delivery is unreliable.

        Args:
            event_types: Filter by event type (issue_created, run_started, etc.)
            limit: Max events to return
            since_timestamp: ISO timestamp to fetch events after

        Returns:
            List of event dicts
        """
        params = {
            "workspace_id": self.workspace_id,
            "limit": limit,
        }
        if event_types:
            params["event_types"] = ",".join(event_types)
        if since_timestamp:
            params["since"] = since_timestamp

        result = self._request("GET", "/events", params=params)
        return result.get("events", [])

    def create_comment(
        self, issue_id: str, text: str, author: Optional[str] = None
    ) -> str:
        """Add a comment to an issue."""
        payload = {
            "text": text,
            "author": author or "assistx",
        }
        result = self._request(
            "POST", f"/issues/{issue_id}/comments", json=payload
        )
        return result.get("id", "")

    def health_check(self) -> bool:
        """Check if Paperclip API is accessible."""
        try:
            self._request("GET", "/health")
            return True
        except Exception as e:
            logger.error(f"Paperclip health check failed: {e}")
            return False
