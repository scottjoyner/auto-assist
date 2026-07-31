from __future__ import annotations

import base64
import ipaddress
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .runtime_projection import projection_checksum, projection_signature


def private_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower().strip("[]").rstrip(".")
    if host in {"localhost", "host.docker.internal", "gateway.docker.internal"}:
        return True
    if host.endswith((".lan", ".local", ".internal", ".ts.net")):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def _request_json(
    method: str,
    url: str,
    *,
    auth: tuple[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    request = urllib.request.Request(url, method=method)
    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(body, separators=(",", ":")).encode()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"snapshot request failed with HTTP {exc.code}: {raw[:500]}") from exc
    except OSError as exc:
        raise RuntimeError(f"snapshot request failed: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("snapshot endpoint returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("snapshot endpoint JSON root is not an object")
    return value


def verify_projection(document: dict[str, Any], secret: str) -> None:
    if not secret:
        raise ValueError("runtime projection HMAC secret is required")
    checksum = projection_checksum(document)
    if str(document.get("checksum") or "") != checksum:
        raise ValueError("runtime projection checksum mismatch")
    try:
        generation = int(document.get("generation") or 0)
        generated_at_ms = int(document.get("generated_at_ms") or 0)
        expires_at_ms = int(document.get("expires_at_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime projection lease is invalid") from exc
    signature = projection_signature(
        generation,
        checksum,
        generated_at_ms,
        expires_at_ms,
        secret,
    )
    if str(document.get("signature") or "") != signature:
        raise ValueError("runtime projection signature mismatch")


class RecoverySnapshotReplicator:
    def __init__(
        self,
        *,
        source_url: str,
        target_url: str,
        source_auth: tuple[str, str],
        target_auth: tuple[str, str],
        secret: str,
        snapshot_path: str | Path,
    ) -> None:
        self.source_url = source_url.rstrip("/")
        self.target_url = target_url.rstrip("/")
        self.source_auth = source_auth
        self.target_auth = target_auth
        self.secret = secret
        self.snapshot_path = Path(snapshot_path)
        if not private_http_url(self.source_url) or not private_http_url(self.target_url):
            raise ValueError("recovery snapshot URLs must be private or loopback")

    def replicate(self) -> dict[str, Any]:
        document = _request_json(
            "GET",
            self.source_url + "/api/router/runtime-projection",
            auth=self.source_auth,
        )
        verify_projection(document, self.secret)
        self._write_snapshot(document)
        accepted = _request_json(
            "POST",
            self.target_url + "/api/degraded/runtime-projection/publish",
            auth=self.target_auth,
            body=document,
        )
        return {
            "ok": True,
            "generation": document.get("generation"),
            "checksum": document.get("checksum"),
            "expires_at_ms": document.get("expires_at_ms"),
            "target_record_id": accepted.get("record_id"),
            "snapshot_path": str(self.snapshot_path),
        }

    def _write_snapshot(self, document: dict[str, Any]) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=self.snapshot_path.name + ".",
            dir=str(self.snapshot_path.parent),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.snapshot_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
