"""HarnessRouter/UHP adapter with explicit authority guards.

Discovery is read-only. Task submission and cancellation require both a config
canary flag and an explicit per-call opt-in; AssistX retains routing authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable
from urllib import error, request


@dataclass(frozen=True)
class HarnessRouterConfig:
    base_url: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    timeout_seconds: int = 10
    canary_enabled: bool = False
    allowed_harness_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessLifecycleReceipt:
    response_id: str
    status: str
    model: str | None
    session_id: str | None
    previous_response_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    error: Any | None
    raw: dict[str, Any]


class HarnessRouterAdapter:
    def __init__(
        self,
        config: HarnessRouterConfig,
        *,
        request_fn: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None,
    ) -> None:
        if not config.base_url.strip():
            raise ValueError("base_url is required")
        self.config = config
        self._request_fn = request_fn or self._http_request

    def discover(self) -> dict[str, Any]:
        harnesses = self._request_fn("GET", "/v1/harnesses", None)
        models = self._request_fn("GET", "/v1/models", None)
        return {
            "status": "ok",
            "authority": "discovery-only",
            "canary_enabled": self.config.canary_enabled,
            "harnesses": harnesses,
            "models": models,
        }

    def submit_canary(
        self,
        prompt: str,
        *,
        harness_id: str,
        model: str,
        explicit_opt_in: bool,
    ) -> HarnessLifecycleReceipt:
        self._require_canary(explicit_opt_in=explicit_opt_in, harness_id=harness_id)
        if not prompt.strip():
            raise ValueError("prompt is required")
        if not model.strip():
            raise ValueError("model is required")
        body = {
            "input": prompt,
            "metadata": {"harness_id": harness_id},
            "model": model,
            "stream": False,
            "background": True,
        }
        payload = self._request_fn("POST", "/v1/responses", body)
        return self._receipt(payload)

    def get_response(self, response_id: str) -> HarnessLifecycleReceipt:
        if not response_id.strip():
            raise ValueError("response_id is required")
        payload = self._request_fn("GET", f"/v1/responses/{response_id}", None)
        return self._receipt(payload)

    def cancel_canary(self, response_id: str, *, explicit_opt_in: bool,
                      harness_id: str) -> HarnessLifecycleReceipt:
        self._require_canary(explicit_opt_in=explicit_opt_in, harness_id=harness_id)
        if not response_id.strip():
            raise ValueError("response_id is required")
        payload = self._request_fn("POST", f"/v1/responses/{response_id}/cancel", {})
        return self._receipt(payload)

    def _require_canary(self, *, explicit_opt_in: bool, harness_id: str) -> None:
        if not self.config.canary_enabled:
            raise PermissionError("HarnessRouter canary dispatch is disabled by config")
        if not explicit_opt_in:
            raise PermissionError("HarnessRouter canary dispatch requires explicit per-call opt-in")
        allowed = self.config.allowed_harness_ids
        if allowed and harness_id not in allowed:
            raise PermissionError(f"harness_id is not allowlisted for canary use: {harness_id}")

    @staticmethod
    def _receipt(payload: dict[str, Any]) -> HarnessLifecycleReceipt:
        if not isinstance(payload, dict):
            raise ValueError("HarnessRouter response must be a JSON object")
        response_id = str(payload.get("id") or "").strip()
        if not response_id:
            raise ValueError("HarnessRouter response id is required")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return HarnessLifecycleReceipt(
            response_id=response_id,
            status=str(payload.get("status") or "unknown"),
            model=(str(payload["model"]) if payload.get("model") is not None else None),
            session_id=(str(metadata["session_id"]) if metadata.get("session_id") else None),
            previous_response_id=(
                str(payload["previous_response_id"])
                if payload.get("previous_response_id") else None
            ),
            input_tokens=(int(usage["input_tokens"]) if usage.get("input_tokens") is not None else None),
            output_tokens=(int(usage["output_tokens"]) if usage.get("output_tokens") is not None else None),
            total_tokens=(int(usage["total_tokens"]) if usage.get("total_tokens") is not None else None),
            error=payload.get("error"),
            raw=payload,
        )

    def _http_request(self, method: str, path: str,
                      body: dict[str, Any] | None) -> dict[str, Any]:
        base = self.config.base_url.rstrip("/")
        if not base.endswith("/api/harness"):
            base = base + "/api/harness"
        url = base + path
        headers = {"accept": "application/json"}
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HarnessRouter HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"HarnessRouter connection failed: {exc.reason}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("HarnessRouter returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("HarnessRouter returned a non-object JSON payload")
        return payload
