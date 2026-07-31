from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ContinuityReplicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplicationDocument:
    name: str
    source_url: str
    validator: str
    required: bool = True
    max_ttl_ms: int = 120_000


def _request_json(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    data: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.data = json.dumps(dict(data), separators=(",", ":")).encode()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload
    except (OSError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


def _read_secret(value: str | None, path_value: str | None) -> str:
    if path_value:
        path = Path(path_value).expanduser().resolve()
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ContinuityReplicationError(
                f"continuity replication secret file must be mode 0600 or stricter: {path}"
            )
        return path.read_text(encoding="utf-8").strip()
    return str(value or "").strip()


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _basic_header(user: str, password: str) -> str:
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {encoded}"


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def validate_runtime_projection(payload: Mapping[str, Any], *, now: int | None = None) -> int:
    current = int(now if now is not None else time.time() * 1000)
    if not isinstance(payload.get("providers"), list) or not payload.get("providers"):
        raise ContinuityReplicationError("runtime projection contains no providers")
    for field in ("checksum", "signature", "generation", "generated_at_ms", "expires_at_ms"):
        if payload.get(field) in {None, ""}:
            raise ContinuityReplicationError(f"runtime projection is missing {field}")
    expiry = int(payload["expires_at_ms"])
    if expiry <= current + 5_000:
        raise ContinuityReplicationError("runtime projection is expired or too close to expiry")
    if int(payload.get("generated_at_ms") or 0) > current + 60_000:
        raise ContinuityReplicationError("runtime projection timestamp is in the future")
    for provider in payload["providers"]:
        if not isinstance(provider, Mapping):
            raise ContinuityReplicationError("runtime projection provider is invalid")
        if not provider.get("runtime_instance_id") or not provider.get("access_urls"):
            raise ContinuityReplicationError("runtime projection provider identity is incomplete")
        if not provider.get("models"):
            raise ContinuityReplicationError("runtime projection provider has no model instances")
    return expiry


def validate_context_projection(payload: Mapping[str, Any], *, now: int | None = None) -> int:
    current = int(now if now is not None else time.time() * 1000)
    contexts = payload.get("contexts")
    if contexts is None:
        contexts = payload.get("manifests")
    if not isinstance(contexts, list):
        raise ContinuityReplicationError("context projection contexts must be a list")
    encoded = json.dumps(contexts, separators=(",", ":")).lower()
    for forbidden in ('"prompt"', '"messages"', '"token_ids"', '"raw_context"'):
        if forbidden in encoded:
            raise ContinuityReplicationError("context projection contains forbidden raw context material")
    expiry = int(payload.get("expires_at_ms") or current + 120_000)
    if expiry <= current + 5_000:
        raise ContinuityReplicationError("context projection is expired or too close to expiry")
    return expiry


def validate_generic_document(payload: Mapping[str, Any], *, now: int | None = None) -> int:
    current = int(now if now is not None else time.time() * 1000)
    expiry = int(payload.get("expires_at_ms") or current + 60_000)
    if expiry <= current + 5_000:
        raise ContinuityReplicationError("document is expired or too close to expiry")
    return expiry


VALIDATORS: dict[str, Callable[..., int]] = {
    "runtime_projection": validate_runtime_projection,
    "context_projection": validate_context_projection,
    "generic": validate_generic_document,
}


class ProjectionReplicator:
    def __init__(
        self,
        *,
        documents: Iterable[ReplicationDocument],
        target_urls: Iterable[str],
        target_token: str,
        expected_cluster_id: str,
        expected_controller_ids: Iterable[str] = (),
        source_headers: Mapping[str, str] | None = None,
        interval_seconds: float = 20.0,
        http: Callable[..., tuple[int, Any]] = _request_json,
    ) -> None:
        self.documents = list(documents)
        if not self.documents:
            raise ContinuityReplicationError("at least one replication document is required")
        self.target_urls = [str(url).rstrip("/") for url in target_urls if str(url).strip()]
        if not self.target_urls:
            raise ContinuityReplicationError("at least one continuity target URL is required")
        if len(target_token) < 16:
            raise ContinuityReplicationError("continuity target token must contain at least 16 characters")
        self.target_token = target_token
        self.expected_cluster_id = expected_cluster_id
        self.expected_controller_ids = {str(value) for value in expected_controller_ids if str(value)}
        self.source_headers = dict(source_headers or {})
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.http = http
        self.active_target = self.target_urls[0]
        self.last_digests: dict[str, str] = {}
        self.last_refresh_ms: dict[str, int] = {}
        self.stop_requested = False

    def request_stop(self, *_args: Any) -> None:
        self.stop_requested = True

    def _target_request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> Any:
        ordered = [self.active_target] + [url for url in self.target_urls if url != self.active_target]
        failures = []
        for base in ordered:
            status, payload = self.http(
                method,
                f"{base}{path}",
                headers={"X-Continuity-Token": self.target_token},
                data=data,
                timeout=10,
            )
            if status == 200:
                self.active_target = base
                return payload
            failures.append({"url": base, "status": status, "payload": payload})
        raise ContinuityReplicationError(f"all continuity targets failed: {failures}")

    def _target_status(self) -> dict[str, Any]:
        payload = self._target_request("GET", "/v1/continuity/status")
        if not isinstance(payload, Mapping):
            raise ContinuityReplicationError("continuity target status is invalid")
        if payload.get("cluster_id") != self.expected_cluster_id:
            raise ContinuityReplicationError("continuity target cluster mismatch")
        controller = str(payload.get("node_id") or "")
        if self.expected_controller_ids and controller not in self.expected_controller_ids:
            raise ContinuityReplicationError("continuity target controller mismatch")
        return dict(payload)

    def _source_document(self, document: ReplicationDocument) -> dict[str, Any]:
        status, payload = self.http(
            "GET",
            document.source_url,
            headers=self.source_headers,
            timeout=15,
        )
        if status != 200 or not isinstance(payload, Mapping):
            raise ContinuityReplicationError(
                f"source document {document.name} failed with HTTP {status}"
            )
        return dict(payload)

    def replicate_once(self) -> dict[str, Any]:
        target = self._target_status()
        epoch = int(target.get("epoch") or 0)
        current = int(time.time() * 1000)
        outcomes = []
        for document in self.documents:
            try:
                payload = self._source_document(document)
                validator = VALIDATORS.get(document.validator)
                if not validator:
                    raise ContinuityReplicationError(
                        f"unknown replication validator: {document.validator}"
                    )
                expiry = validator(payload, now=current)
                ttl_ms = min(document.max_ttl_ms, expiry - current)
                if ttl_ms < 5_000:
                    raise ContinuityReplicationError("replication TTL is too short")
                digest = _canonical_digest(payload)
                last_refresh = self.last_refresh_ms.get(document.name, 0)
                refresh_due = current - last_refresh >= max(5_000, ttl_ms // 2)
                changed = self.last_digests.get(document.name) != digest
                if changed or refresh_due:
                    encoded_name = urllib.parse.quote(document.name, safe="")
                    self._target_request(
                        "PUT",
                        f"/v1/continuity/documents/{encoded_name}",
                        data={"payload": payload, "epoch": epoch, "ttl_ms": ttl_ms},
                    )
                    self.last_digests[document.name] = digest
                    self.last_refresh_ms[document.name] = current
                    action = "updated" if changed else "refreshed"
                else:
                    action = "unchanged"
                outcomes.append(
                    {
                        "name": document.name,
                        "ok": True,
                        "action": action,
                        "digest": digest,
                        "ttl_ms": ttl_ms,
                    }
                )
            except Exception as exc:
                outcomes.append(
                    {
                        "name": document.name,
                        "ok": False,
                        "required": document.required,
                        "error": str(exc)[:1000],
                    }
                )
        required_failures = [item for item in outcomes if not item["ok"] and item.get("required")]
        return {
            "ok": not required_failures,
            "epoch": epoch,
            "target": self.active_target,
            "documents": outcomes,
        }

    def loop(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        backoff = self.interval_seconds
        while not self.stop_requested:
            try:
                result = self.replicate_once()
                print(json.dumps(result, sort_keys=True), flush=True)
                backoff = self.interval_seconds if result["ok"] else min(60.0, backoff * 2)
            except Exception as exc:
                print(
                    json.dumps(
                        {"ok": False, "error": str(exc)[:1000], "error_type": type(exc).__name__},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                backoff = min(60.0, max(self.interval_seconds, backoff * 2))
            end = time.monotonic() + backoff
            while not self.stop_requested and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))
        return 0


def documents_from_env(env: Mapping[str, str]) -> list[ReplicationDocument]:
    configured = str(env.get("ASSISTX_CONTINUITY_REPLICATION_DOCUMENTS_JSON") or "").strip()
    if configured:
        try:
            raw = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise ContinuityReplicationError("replication document JSON is invalid") from exc
        if not isinstance(raw, list):
            raise ContinuityReplicationError("replication document JSON must be a list")
        return [
            ReplicationDocument(
                name=str(item["name"]),
                source_url=str(item["source_url"]),
                validator=str(item.get("validator") or "generic"),
                required=bool(item.get("required", True)),
                max_ttl_ms=max(5_000, min(int(item.get("max_ttl_ms") or 120_000), 900_000)),
            )
            for item in raw
            if isinstance(item, Mapping)
        ]

    base = str(env.get("ASSISTX_CONTINUITY_REPLICATION_SOURCE_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    return [
        ReplicationDocument(
            "runtime-projection",
            f"{base}/api/router/runtime-projection",
            "runtime_projection",
            True,
            120_000,
        ),
        ReplicationDocument(
            "context-projection",
            f"{base}/api/router/context-projection",
            "context_projection",
            False,
            120_000,
        ),
    ]


def build_replicator_from_env(env: Mapping[str, str] | None = None) -> ProjectionReplicator:
    source = env or os.environ
    target_token = _read_secret(
        source.get("ASSISTX_CONTINUITY_REPLICATION_TARGET_TOKEN"),
        source.get("ASSISTX_CONTINUITY_REPLICATION_TARGET_TOKEN_FILE"),
    )
    source_user = str(source.get("ASSISTX_CONTINUITY_REPLICATION_SOURCE_USER") or "")
    source_pass = _read_secret(
        source.get("ASSISTX_CONTINUITY_REPLICATION_SOURCE_PASS"),
        source.get("ASSISTX_CONTINUITY_REPLICATION_SOURCE_PASS_FILE"),
    )
    headers = {}
    if source_user and source_pass:
        headers["Authorization"] = _basic_header(source_user, source_pass)
    return ProjectionReplicator(
        documents=documents_from_env(source),
        target_urls=_csv(source.get("ASSISTX_CONTINUITY_REPLICATION_TARGET_URLS")),
        target_token=target_token,
        expected_cluster_id=str(source.get("ASSISTX_CONTINUITY_CLUSTER_ID") or "assistx-fleet"),
        expected_controller_ids=_csv(source.get("ASSISTX_CONTINUITY_EXPECTED_CONTROLLER_IDS")),
        source_headers=headers,
        interval_seconds=float(source.get("ASSISTX_CONTINUITY_REPLICATION_INTERVAL_SECONDS", "20")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Replicate fresh AssistX projections to the Beelink continuity plane")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    replicator = build_replicator_from_env()
    if args.once:
        result = replicator.replicate_once()
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 2
    return replicator.loop()


if __name__ == "__main__":
    raise SystemExit(main())
