"""
Paperclip API client for AssistX.

Handles creation of issues, agent management, and event polling from Paperclip.
"""

from __future__ import annotations

import os
import requests
import time
import re
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class PaperclipClient:
    """
    Client for interacting with the Paperclip API.

    AssistX remains task authority; Paperclip is the supported non-realtime
    execution route for the current cutover release.
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
        self.company_id = self.workspace_id  # Paperclip calls these "companies"

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
                if resp.status_code >= 400:
                    body = ""
                    try:
                        body = resp.text[:4000]
                    except Exception:
                        body = "<unavailable>"
                    msg = f"{resp.status_code} {resp.reason} for {method} {url}: {body}"
                    err = requests.HTTPError(msg, response=resp)
                    raise err
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
        priority: str = "medium",
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
            priority: Issue priority (low, medium, high, critical)
            assignee_id: Optional Paperclip agent ID

        Returns:
            Paperclip issue ID
        """
        resolved_assignee = self.resolve_agent_id(assignee_id)
        payload = {
            "title": title,
            "description": description,
            "assigneeAgentId": resolved_assignee,
            "priority": priority,
            "metadata": {
                "assistx_task_id": task_id,
                "assistx_context_packet_id": context_packet_id,
                "required_capabilities": capabilities,
                "source": "assistx-migration",
            },
        }
        result = self._request("POST", f"/companies/{self.company_id}/issues", json=payload)
        return result["id"]

    def resolve_agent_id(self, agent_ref: Optional[str]) -> Optional[str]:
        """
        Resolve agent identifiers for assignee fields.

        Accepts:
        - canonical UUID agent IDs
        - exact agent names
        - case-insensitive/sluggified name aliases (e.g. "hermes-local")
        """
        if not agent_ref:
            return None
        ref = agent_ref.strip()
        if not ref:
            return None
        if self._looks_like_uuid(ref):
            return ref
        try:
            agents = self.list_agents()
        except Exception as e:
            logger.warning("Could not resolve Paperclip agent ref '%s': %s", ref, e)
            return agent_ref

        # Exact id or name match first.
        for agent in agents:
            if agent.get("id") == ref or agent.get("name") == ref:
                return agent.get("id")

        norm_ref = self._norm_agent_ref(ref)
        for agent in agents:
            name = str(agent.get("name") or "")
            if self._norm_agent_ref(name) == norm_ref:
                return agent.get("id")

        logger.warning("Paperclip agent ref '%s' did not resolve; using raw value", ref)
        return agent_ref

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        return bool(
            re.match(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
                value,
            )
        )

    @staticmethod
    def _norm_agent_ref(value: str) -> str:
        return "".join(ch.lower() for ch in value if ch.isalnum())

    def get_issue(self, issue_id: str) -> Dict[str, Any]:
        """Fetch issue details."""
        return self._request("GET", f"/issues/{issue_id}")

    def update_issue(self, issue_id: str, **kwargs) -> Dict[str, Any]:
        """Update issue fields."""
        return self._request("PATCH", f"/issues/{issue_id}", json=kwargs)

    def assign_issue(self, issue_id: str, agent_id: str) -> bool:
        """Assign issue to an agent via PATCH."""
        result = self._request("PATCH", f"/issues/{issue_id}", json={"assigneeAgentId": agent_id})
        return result.get("assigneeAgentId") == agent_id

    def list_issues(
        self,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List issues with optional filters."""
        params = {
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status
        if agent_id:
            params["agent_id"] = agent_id

        result = self._request("GET", f"/companies/{self.company_id}/issues", params=params)
        return self._coerce_list(result, "issues")

    def list_agents(
        self, company_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List registered agents in company.

        Returns list of agent dicts with id, name, capabilities, status, etc.
        """
        cid = company_id or self.company_id
        result = self._request("GET", f"/companies/{cid}/agents")
        return self._coerce_list(result, "agents")

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """Get agent details."""
        return self._request("GET", f"/agents/{agent_id}")

    def list_runs(
        self,
        issue_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List runs (executions) for an issue."""
        params = {
            "limit": limit,
        }
        if agent_id:
            params["agent_id"] = agent_id

        if issue_id:
            result = self._request("GET", f"/issues/{issue_id}/runs", params=params)
        else:
            result = self._request("GET", f"/companies/{self.company_id}/live-runs", params=params)
        runs = self._coerce_list(result, "runs")
        return runs if runs else self._coerce_list(result, "items")

    def get_run(self, run_id: str) -> Dict[str, Any]:
        """Get run details."""
        return self._request("GET", f"/heartbeat-runs/{run_id}")

    def get_run_output(self, run_id: str) -> str:
        """Get run output/logs."""
        result = self._request("GET", f"/heartbeat-runs/{run_id}/log")
        return result.get("output", result.get("log", result.get("content", "")))

    def poll_events(
        self,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
        since_timestamp: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Poll for issue updates (change-based polling, not event stream).

        Paperclip has no dedicated event-stream endpoint. This method
        polls issues for status changes as a stand-in.
        """
        params = {
            "limit": limit,
        }
        if since_timestamp:
            params["since"] = since_timestamp

        if event_types:
            logger.warning("poll_events event_types filtering not supported in this Paperclip version")

        result = self._request("GET", f"/companies/{self.company_id}/issues", params=params)
        return self._coerce_list(result, "issues")

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
        return result.get("id", str(result.get("commentId", "")))

    def health_check(self) -> bool:
        """Check if Paperclip API is accessible."""
        try:
            self._request("GET", "/health")
            return True
        except Exception as e:
            logger.error(f"Paperclip health check failed: {e}")
            return False
    @staticmethod
    def _coerce_list(result: Any, key: str) -> List[Dict[str, Any]]:
        if isinstance(result, list):
            return [x for x in result if isinstance(x, dict)]
        if isinstance(result, dict):
            value = result.get(key, [])
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return []
