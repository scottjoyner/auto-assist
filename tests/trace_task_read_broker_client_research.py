"""Draft worker-side read-only compatibility shim for Neo4jClient.get_task.

Supports only the tested Task.id/Task.status projection; never falls back to
direct Neo4j. Does not claim full Neo4jClient feature compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request

_TASK_ID = re.compile(r"^fixture-task-[A-Za-z0-9_-]{4,48}$")


class BrokerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskReadBrokerClient:
    trusted_broker_origin: str
    bearer: str

    def __post_init__(self):
        if (not self.trusted_broker_origin.startswith("http://assistx-broker-")
                and not self.trusted_broker_origin.startswith(
                    "http://assistx-broker-gateway-")):
            # The research broker address MUST come from deployment config,
            # never request JSON. Production requires mTLS+identity binding.
            raise ValueError("UNTRUSTED_RESEARCH_BROKER_ORIGIN")
        if not self.bearer or len(self.bearer) < 16:
            raise ValueError("MISSING_BROKER_AUTH")

    def get_task(self, task_id: str) -> dict | None:
        """Original two-field read slice, no policy claims."""
        return self._get_with_plan(task_id, "get_task_read")

    def get_task_claim_guard(self, task_id: str) -> dict | None:
        """Explicit claim/recovery policy read, NOT a full get_task."""
        return self._get_with_plan(task_id, "get_task_claim_guard")

    def _get_with_plan(self, task_id: str, plan_id: str) -> dict | None:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError("INVALID_TASK_LOOKUP_ID")
        if plan_id not in ("get_task_read", "get_task_claim_guard"):
            raise ValueError("UNAPPROVED_BROKER_PLAN")
        encoded = json.dumps({
            "plan_id": plan_id, "parameters": {"id": task_id}
        }, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.trusted_broker_origin + "/v1/plan",
            data=encoded, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.bearer,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                envelope = json.loads(response.read(8192))
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            raise BrokerUnavailable("BROKER_REQUEST_DENIED_OR_UNCERTAIN") from None
        if not isinstance(envelope, dict) or set(envelope) != {"task"}:
            raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
        value = envelope["task"]
        if value is None:
            return None
        if not isinstance(value, dict) or (
                type(value.get("id")) is not str
                or type(value.get("status")) is not str
                or value["id"] != task_id):
            raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
        if plan_id == "get_task_read":
            if set(value) != {"id", "status"}:
                raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
        else:
            allowed = {"id", "status", "payload", "payload_json",
                       "queue_class", "required_capabilities"}
            if set(value) != allowed:
                raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
            if (value.get("payload") is not None and
                    type(value["payload"]) is not str):
                raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
            if (value.get("payload_json") is not None and
                    type(value["payload_json"]) is not str):
                raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
            if (value.get("queue_class") is not None and
                    type(value["queue_class"]) is not str):
                raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
            caps = value.get("required_capabilities")
            if caps is not None and (
                    type(caps) is not list or len(caps) > 32 or
                    any(type(x) is not str or len(x) > 128 for x in caps)):
                raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
        return dict(value)
