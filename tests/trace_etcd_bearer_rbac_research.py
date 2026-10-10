"""Research-only etcd v3 JSON-gateway bearer-token client.

Important: etcd gRPC-gateway does NOT propagate TLS Common Name identity
into etcd RBAC. A client certificate only protects transport; scoped KV
authorization must use an authenticated etcd token (or native gRPC).
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trace_etcd_quorum_fence_research import (
    EtcdTLS, FenceRefused, b64, canon
)


class EtcdBearerTLS(EtcdTLS):
    """mTLS PLUS RBAC bearer authorization. Never silently fall back."""

    def __init__(self, endpoint: str, ca: str, cert: str, key: str,
                 token: str):
        super().__init__(endpoint, ca, cert, key)
        if not isinstance(token, str) or not token.strip():
            raise ValueError("MISSING_RBAC_BEARER_CREDENTIAL")
        self._token = token

    def _post(self, path: str, body: dict) -> dict:
        request = Request(
            self.endpoint + path,
            data=canon(body), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": self._token},
        )
        try:
            with urlopen(request, timeout=3, context=self.context) as response:
                return json.loads(response.read(2_000_000))
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise FenceRefused("ETCD_RBAC_ACCESS_DENIED") from None
            raise FenceRefused("ETCD_RBAC_OR_QUORUM_OPERATION_FAILED") from None
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise FenceRefused("ETCD_RBAC_OR_QUORUM_OPERATION_FAILED") from None

    def __repr__(self) -> str:
        return f"<EtcdBearerTLS endpoint={self.endpoint!r} credential=REDACTED>"


def require_etcd_bearer(token: str) -> None:
    if not token or not isinstance(token, str):
        raise FenceRefused("NO_ETCD_RBAC_TOKEN")
