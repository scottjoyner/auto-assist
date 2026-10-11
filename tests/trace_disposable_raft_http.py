"""Disposable-only HTTP transport for isolated Raft research network.

No production TLS claim; use ONLY on temporary --internal Docker network,
never as a replacement for authenticated etcd transport or scoped writer RBAC.
"""
from __future__ import annotations
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

class ResearchHTTP:
    def __init__(self, endpoint: str):
        if not endpoint.startswith("http://assistx-quorum-"):
            raise ValueError("UNTRUSTED_DISPOSABLE_RAFT_ENDPOINT")
        self.endpoint = endpoint.rstrip("/")
    def _request(self, path: str, payload: dict) -> dict:
        try:
            with urlopen(Request(
                self.endpoint + path,
                method="POST",
                data=json.dumps(payload, separators=(",", ":")).encode(),
                headers={"Content-Type": "application/json"},
            ), timeout=3) as response:
                return json.loads(response.read(1_000_000))
        except (URLError, HTTPError, TimeoutError, OSError,
                ValueError, UnicodeError):
            from trace_etcd_quorum_fence_research import FenceRefused
            raise FenceRefused("DISPOSABLE_QUORUM_UNCERTAIN") from None
    def range(self, key: str) -> dict:
        from trace_etcd_quorum_fence_research import b64
        return self._request("/v3/kv/range", {"key": b64(key)})
    def txn(self, req: dict) -> dict:
        return self._request("/v3/kv/txn", req)
