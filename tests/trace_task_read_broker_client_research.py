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
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError("INVALID_TASK_LOOKUP_ID")
        encoded = json.dumps({
            "plan_id": "get_task_read", "parameters": {"id": task_id}
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
        if (not isinstance(value, dict) or set(value) != {"id", "status"}
                or type(value.get("id")) is not str
                or type(value.get("status")) is not str
                or value["id"] != task_id):
            raise BrokerUnavailable("BROKER_RESPONSE_UNTRUSTED")
        return dict(value)
