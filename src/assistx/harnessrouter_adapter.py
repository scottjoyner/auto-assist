"""HarnessRouter/UHP adapter with explicit authority guards.

Discovery is read-only. Task submission and cancellation require both a config
canary flag and an explicit per-call opt-in; AssistX retains routing authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
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
    error: Any | None = field(repr=False)
    raw: dict[str, Any] = field(repr=False)


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
        # Process-local ownership; never cancel arbitrary response IDs.
        self._owned_responses: dict[str, str] = {}

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

    def discover_protocol(self) -> dict[str, Any]:
        """Read-only UHP discovery. Advertised capabilities are NOT conformance proof."""
        payload = self._request_fn("GET", "/v1/uhp", None)
        if not isinstance(payload, dict) or payload.get("object") != "uhp.discovery" or payload.get("protocol") != "uhp":
            raise ValueError("UHP discovery identity mismatch")
        versions = payload.get("versions")
        default = payload.get("default_version")
        if (not isinstance(versions, list) or not versions
                or any(not isinstance(v, str) or re.fullmatch(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]", v) is None for v in versions)
                or len(set(versions)) != len(versions)
                or not isinstance(default, str) or default not in versions):
            raise ValueError("UHP version negotiation is malformed")
        claimed = payload.get("conformance_class")
        if claimed not in ("core", "extended", "full"):
            raise ValueError("UHP conformance class declaration is invalid")
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict) or any(
            not isinstance(k, str) or type(v) is not bool
            for k, v in capabilities.items()
        ):
            raise ValueError("UHP capabilities must be named booleans")
        if claimed in ("extended", "full") and not all(
            capabilities.get(k) is True for k in ("files_input", "files_output", "session_listing")
        ):
            raise ValueError("UHP claimed class contradicts capability discovery")
        return {
            "protocol": "uhp",
            "default_version": default,
            "versions": versions,
            "claimed_class": claimed,
            "capabilities": capabilities,
            "tested_contract_version": "2026-10-19",
            "matches_tested_contract": "2026-10-19" in versions,
            "conformance_independently_verified": False,
            "authority": "discovery-only",
            "canary_enabled": self.config.canary_enabled,
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
        receipt = self._receipt(payload)
        if receipt.model is not None and receipt.model != model:
            metadata = payload.get("metadata")
            # Never mis-account a substituted model as the requested one.
            if not (isinstance(metadata, dict)
                    and metadata.get("model_fallback") is True
                    and metadata.get("requested_model") == model
                    and isinstance(metadata.get("model_fallback_reason"), str)
                    and metadata["model_fallback_reason"].strip()):
                raise ValueError("HarnessRouter substituted a model without a complete fallback receipt")
        if receipt.response_id in self._owned_responses:
            # An id returned by different submissions must never silently
            # rebind the cancellation authority to a different harness.
            raise ValueError("HarnessRouter reused an already owned response id")
        self._owned_responses[receipt.response_id] = harness_id
        return receipt

    def get_response(self, response_id: str) -> HarnessLifecycleReceipt:
        self._require_owned_response(response_id)
        payload = self._request_fn("GET", f"/v1/responses/{response_id}", None)
        receipt = self._receipt(payload)
        if receipt.response_id != response_id:
            raise ValueError("HarnessRouter returned a different response id")
        return receipt

    def cancel_canary(self, response_id: str, *, explicit_opt_in: bool,
                      harness_id: str) -> HarnessLifecycleReceipt:
        self._require_canary(explicit_opt_in=explicit_opt_in, harness_id=harness_id)
        owner = self._require_owned_response(response_id)
        if owner != harness_id:
            raise PermissionError("response was not submitted for the selected harness")
        payload = self._request_fn("POST", f"/v1/responses/{response_id}/cancel", {})
        receipt = self._receipt(payload)
        if receipt.response_id != response_id:
            raise ValueError("HarnessRouter cancelled a different response id")
        # Acknowledgement is not proof that the running task has stopped.
        return receipt

    def _require_canary(self, *, explicit_opt_in: bool, harness_id: str) -> None:
        if not self.config.canary_enabled:
            raise PermissionError("HarnessRouter canary dispatch is disabled by config")
        if not explicit_opt_in:
            raise PermissionError("HarnessRouter canary dispatch requires explicit per-call opt-in")
        allowed = self.config.allowed_harness_ids
        if not allowed or harness_id not in allowed:
            raise PermissionError("harness_id must be explicitly allowlisted for canary use")

    def _require_owned_response(self, response_id: str) -> str:
        if not isinstance(response_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", response_id) is None:
            raise ValueError("response_id must be a canonical identifier")
        owner = self._owned_responses.get(response_id)
        if owner is None:
            raise PermissionError("response_id is not owned by this canary adapter")
        return owner

    @staticmethod
    def _token_count(value: Any, field_name: str) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value < 0:
            raise ValueError(f"invalid token count for {field_name}")
        return value

    @staticmethod
    def _receipt(payload: dict[str, Any]) -> HarnessLifecycleReceipt:
        if not isinstance(payload, dict):
            raise ValueError("HarnessRouter response must be a JSON object")
        response_id = payload.get("id")
        if not isinstance(response_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", response_id) is None:
            raise ValueError("HarnessRouter response id must be a canonical identifier")
        status = payload.get("status")
        if not isinstance(status, str) or status not in {"in_progress", "completed", "failed", "incomplete", "cancelled"}:
            raise ValueError("HarnessRouter returned an unknown lifecycle status")
        if status == "failed" and payload.get("error") is None:
            raise ValueError("a failed HarnessRouter task requires an error")
        metadata = payload.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("HarnessRouter metadata must be an object")
        usage = payload.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError("HarnessRouter usage must be an object")
        return HarnessLifecycleReceipt(
            response_id=response_id,
            status=status,
            model=(str(payload["model"]) if payload.get("model") is not None else None),
            session_id=(str(metadata["session_id"]) if metadata.get("session_id") else None),
            previous_response_id=(
                str(payload["previous_response_id"])
                if payload.get("previous_response_id") else None
            ),
            input_tokens=HarnessRouterAdapter._token_count(usage.get("input_tokens"), "input_tokens"),
            output_tokens=HarnessRouterAdapter._token_count(usage.get("output_tokens"), "output_tokens"),
            total_tokens=HarnessRouterAdapter._token_count(usage.get("total_tokens"), "total_tokens"),
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
            # An untrusted body may reflect a token or contain private traces.
            raise RuntimeError(f"HarnessRouter HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise RuntimeError("HarnessRouter connection failed") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("HarnessRouter returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("HarnessRouter returned a non-object JSON payload")
        return payload
