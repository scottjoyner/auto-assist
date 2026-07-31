from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import signal
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any


class ContinuityAgentError(RuntimeError):
    pass


class ContinuityEpochRollback(ContinuityAgentError):
    pass


def _json_request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    data: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Continuity-Token", token)
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


def _read_secret(value: str | None, file_path: str | None) -> str:
    if file_path:
        path = Path(file_path).expanduser().resolve()
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ContinuityAgentError(
                f"continuity token file must be mode 0600 or stricter: {path}"
            )
        return path.read_text(encoding="utf-8").strip()
    return str(value or "").strip()


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _memory_report() -> tuple[int, int]:
    if sys.platform.startswith("linux"):
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) // 1024
        except (OSError, ValueError, IndexError):
            return 0, 0
        return values.get("MemTotal", 0), values.get("MemAvailable", 0)
    try:
        total = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        total = 0
    return total, 0


def _private_host(hostname: str) -> bool:
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    if not addresses:
        return False
    for result in addresses:
        address = result[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed in ipaddress.ip_network("100.64.0.0/10")
        ):
            return False
    return True


def _safe_child(root: Path, candidate: str) -> Path:
    resolved_root = root.expanduser().resolve()
    resolved = Path(candidate).expanduser().resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ContinuityAgentError("path is outside the continuity artifact roots")
    return resolved


def _sha256_file(path: Path, *, max_bytes: int = 256 * 1024 * 1024 * 1024) -> dict[str, Any]:
    if not path.is_file():
        raise ContinuityAgentError("artifact path is not a file")
    size = path.stat().st_size
    if size > max_bytes:
        raise ContinuityAgentError("artifact exceeds configured checksum size")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "bytes": size,
        "sha256": digest.hexdigest(),
    }


class ContinuityClient:
    def __init__(
        self,
        urls: Iterable[str],
        *,
        token: str,
        expected_cluster_id: str,
        expected_controller_ids: Iterable[str] = (),
        http: Callable[..., tuple[int, Any]] = _json_request,
    ) -> None:
        self.urls = [str(url).rstrip("/") for url in urls if str(url).strip()]
        if not self.urls:
            raise ContinuityAgentError("at least one continuity URL is required")
        if len(token) < 16:
            raise ContinuityAgentError("continuity API token must contain at least 16 characters")
        self.token = token
        self.expected_cluster_id = expected_cluster_id
        self.expected_controller_ids = {str(value) for value in expected_controller_ids if str(value)}
        self.http = http
        self.active_url = self.urls[0]

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
        expected: set[int] | None = None,
    ) -> Any:
        expected = expected or {200}
        ordered = [self.active_url] + [url for url in self.urls if url != self.active_url]
        failures = []
        for base in ordered:
            status, payload = self.http(
                method,
                f"{base}{path}",
                token=self.token,
                data=data,
                timeout=10,
            )
            if status in expected:
                self.active_url = base
                return payload
            failures.append({"url": base, "status": status, "payload": payload})
        raise ContinuityAgentError(f"all continuity endpoints failed: {failures}")

    def status(self) -> dict[str, Any]:
        payload = self._request("GET", "/v1/continuity/status")
        if payload.get("cluster_id") != self.expected_cluster_id:
            raise ContinuityAgentError("continuity cluster identity mismatch")
        controller = str(payload.get("node_id") or "")
        if self.expected_controller_ids and controller not in self.expected_controller_ids:
            raise ContinuityAgentError("continuity controller identity mismatch")
        return dict(payload)

    def heartbeat(self, report: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self._request("POST", "/v1/continuity/heartbeat", data=report))

    def claim(self, *, node_id: str, capabilities: list[str], epoch: int, ttl_ms: int) -> dict[str, Any] | None:
        payload = self._request(
            "POST",
            "/v1/continuity/tasks/claim",
            data={
                "node_id": node_id,
                "capabilities": capabilities,
                "epoch": epoch,
                "ttl_ms": ttl_ms,
            },
        )
        task = payload.get("task")
        return dict(task) if isinstance(task, Mapping) else None

    def complete(
        self,
        *,
        task_id: str,
        node_id: str,
        claim_token: str,
        status: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/v1/continuity/tasks/{urllib.parse.quote(task_id, safe='')}/complete",
                data={
                    "node_id": node_id,
                    "claim_token": claim_token,
                    "status": status,
                    "result": dict(result),
                },
            )
        )


class EpochGuard:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def accept(self, epoch: int) -> int:
        requested = int(epoch)
        current = 0
        if self.path.is_file():
            try:
                current = int(json.loads(self.path.read_text(encoding="utf-8")).get("epoch") or 0)
            except (OSError, ValueError, json.JSONDecodeError):
                raise ContinuityAgentError("continuity epoch state is unreadable") from None
        if requested < current:
            raise ContinuityEpochRollback(
                f"continuity epoch rollback rejected: current={current} requested={requested}"
            )
        if requested > current:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"epoch": requested}), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        return requested


class SafeContinuityExecutor:
    DEFAULT_KINDS = {
        "runtime_probe",
        "http_probe",
        "artifact_checksum",
        "backup_verify",
    }

    def __init__(
        self,
        *,
        lmstudio_url: str | None,
        artifact_roots: Iterable[str],
        http_allowlist: Iterable[str],
        allowed_kinds: Iterable[str] | None = None,
        http: Callable[..., tuple[int, Any]] = _json_request,
    ) -> None:
        self.lmstudio_url = str(lmstudio_url or "").rstrip("/")
        self.artifact_roots = [Path(value).expanduser().resolve() for value in artifact_roots]
        self.http_allowlist = [str(value).rstrip("/") for value in http_allowlist]
        self.allowed_kinds = set(allowed_kinds or self.DEFAULT_KINDS)
        self.http = http

    def capabilities(self) -> list[str]:
        values = {"continuity_worker", "http_probe"}
        if self.lmstudio_url:
            values.update({"runtime_probe", "lmstudio"})
        if self.artifact_roots:
            values.update({"artifact_checksum", "backup_verify"})
        return sorted(values)

    def execute(self, task: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(task.get("kind") or "")
        if kind not in self.allowed_kinds:
            raise ContinuityAgentError(f"continuity task kind is not allowlisted: {kind}")
        payload = task.get("payload") or {}
        if not isinstance(payload, Mapping):
            raise ContinuityAgentError("continuity task payload must be an object")
        if kind == "runtime_probe":
            return self._runtime_probe()
        if kind == "http_probe":
            return self._http_probe(payload)
        if kind == "artifact_checksum":
            return self._artifact_checksum(payload)
        if kind == "backup_verify":
            return self._backup_verify(payload)
        raise ContinuityAgentError(f"unsupported continuity task kind: {kind}")

    def _runtime_probe(self) -> dict[str, Any]:
        if not self.lmstudio_url:
            raise ContinuityAgentError("LM Studio URL is not configured")
        status, payload = self.http("GET", f"{self.lmstudio_url}/v1/models", timeout=8)
        if status != 200:
            raise ContinuityAgentError(f"local runtime probe failed with HTTP {status}")
        models = []
        if isinstance(payload, Mapping):
            for item in payload.get("data") or []:
                if isinstance(item, Mapping):
                    models.append(str(item.get("id") or ""))
        return {"status_code": status, "models": sorted(value for value in models if value)}

    def _http_probe(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        target = str(payload.get("url") or "").rstrip("/")
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ContinuityAgentError("HTTP probe URL is invalid")
        if not any(target == prefix or target.startswith(prefix + "/") for prefix in self.http_allowlist):
            raise ContinuityAgentError("HTTP probe target is not allowlisted")
        if not _private_host(parsed.hostname):
            raise ContinuityAgentError("HTTP probe target is not private")
        status, body = self.http("GET", target, timeout=min(15, int(payload.get("timeout") or 5)))
        encoded = json.dumps(body, sort_keys=True, default=str).encode()
        return {
            "status_code": status,
            "body_sha256": hashlib.sha256(encoded).hexdigest(),
            "body_bytes": len(encoded),
        }

    def _resolve_artifact(self, candidate: str) -> Path:
        for root in self.artifact_roots:
            try:
                return _safe_child(root, candidate)
            except ContinuityAgentError:
                continue
        raise ContinuityAgentError("artifact is outside every allowlisted root")

    def _artifact_checksum(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return _sha256_file(self._resolve_artifact(str(payload.get("path") or "")))

    def _backup_verify(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        root = self._resolve_artifact(str(payload.get("path") or ""))
        if not root.is_dir():
            raise ContinuityAgentError("backup path is not a directory")
        required = [str(value) for value in payload.get("required_databases") or ["system", "neo4j"]]
        artifacts = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in {".backup", ".dump", ".json"}
        )
        if not artifacts:
            raise ContinuityAgentError("backup set contains no supported artifacts")
        names = [path.name.lower() for path in artifacts]
        missing = [database for database in required if not any(database.lower() in name for name in names)]
        if missing:
            raise ContinuityAgentError(f"backup set is missing required databases: {missing}")
        records = [_sha256_file(path) for path in artifacts]
        aggregate = hashlib.sha256(
            "\n".join(f"{item['sha256']}  {item['path']}" for item in records).encode()
        ).hexdigest()
        return {
            "artifact_count": len(records),
            "artifact_set_sha256": aggregate,
            "required_databases": required,
            "artifacts": records[:100],
        }


class ContinuityNodeAgent:
    def __init__(
        self,
        *,
        client: ContinuityClient,
        executor: SafeContinuityExecutor,
        node_id: str,
        epoch_guard: EpochGuard,
        poll_interval: float = 10.0,
        claim_ttl_ms: int = 900_000,
        extra_capabilities: Iterable[str] = (),
    ) -> None:
        self.client = client
        self.executor = executor
        self.node_id = node_id
        self.epoch_guard = epoch_guard
        self.poll_interval = max(2.0, float(poll_interval))
        self.claim_ttl_ms = max(30_000, min(int(claim_ttl_ms), 900_000))
        self.capabilities = sorted(set(executor.capabilities()) | {str(value) for value in extra_capabilities})
        self.stop_requested = False

    def request_stop(self, *_args: Any) -> None:
        self.stop_requested = True

    def _heartbeat(self, *, epoch: int, status: str = "healthy") -> dict[str, Any]:
        total, available = _memory_report()
        return self.client.heartbeat(
            {
                "node_id": self.node_id,
                "hostname": platform.node() or self.node_id,
                "status": status,
                "capabilities": self.capabilities,
                "roles": [],
                "active_slots": 0,
                "max_slots": 1,
                "memory_total_mb": total,
                "memory_available_mb": available,
                "runtime_models": [],
                "metadata": {
                    "agent": "assistx.continuity_node_agent",
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "continuity_epoch": epoch,
                    "active_controller_url": self.client.active_url,
                },
            }
        )

    def run_once(self) -> dict[str, Any]:
        status = self.client.status()
        epoch = self.epoch_guard.accept(int(status.get("epoch") or 0))
        heartbeat = self._heartbeat(epoch=epoch)
        task = self.client.claim(
            node_id=self.node_id,
            capabilities=self.capabilities,
            epoch=epoch,
            ttl_ms=self.claim_ttl_ms,
        )
        if not task:
            return {"ok": True, "epoch": epoch, "heartbeat": heartbeat, "task": None}
        try:
            result = self.executor.execute(task)
            final_status = "completed"
        except Exception as exc:
            result = {"error": str(exc)[:1000], "error_type": type(exc).__name__}
            final_status = "failed"
        completed = self.client.complete(
            task_id=str(task["task_id"]),
            node_id=self.node_id,
            claim_token=str(task["claim_token"]),
            status=final_status,
            result=result,
        )
        return {
            "ok": final_status == "completed",
            "epoch": epoch,
            "task_id": task["task_id"],
            "status": final_status,
            "completion": completed,
        }

    def loop(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        backoff = self.poll_interval
        while not self.stop_requested:
            try:
                outcome = self.run_once()
                print(json.dumps(outcome, sort_keys=True), flush=True)
                backoff = self.poll_interval
            except Exception as exc:
                print(
                    json.dumps(
                        {"ok": False, "error": str(exc)[:1000], "error_type": type(exc).__name__},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                backoff = min(60.0, max(self.poll_interval, backoff * 2))
            end = time.monotonic() + backoff
            while not self.stop_requested and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))
        try:
            status = self.client.status()
            epoch = self.epoch_guard.accept(int(status.get("epoch") or 0))
            self._heartbeat(epoch=epoch, status="draining")
        except Exception:
            pass
        return 0


def build_agent_from_env(env: Mapping[str, str] | None = None) -> ContinuityNodeAgent:
    source = env or os.environ
    token = _read_secret(
        source.get("FLEET_CONTINUITY_TOKEN"),
        source.get("FLEET_CONTINUITY_TOKEN_FILE"),
    )
    node_id = str(source.get("FLEET_NODE_ID") or f"{platform.node()}-{platform.machine()}")
    urls = _csv(source.get("FLEET_CONTINUITY_URLS"))
    controller_ids = _csv(source.get("FLEET_CONTINUITY_EXPECTED_CONTROLLER_IDS"))
    roots = _csv(source.get("FLEET_CONTINUITY_ARTIFACT_ROOTS"))
    http_allowlist = _csv(source.get("FLEET_CONTINUITY_HTTP_ALLOWLIST"))
    allowed_kinds = _csv(source.get("FLEET_CONTINUITY_ALLOWED_TASK_KINDS")) or None
    state_dir = Path(source.get("FLEET_CONTINUITY_STATE_DIR", "~/.assistx-continuity")).expanduser()
    return ContinuityNodeAgent(
        client=ContinuityClient(
            urls,
            token=token,
            expected_cluster_id=str(source.get("FLEET_CONTINUITY_CLUSTER_ID") or "assistx-fleet"),
            expected_controller_ids=controller_ids,
        ),
        executor=SafeContinuityExecutor(
            lmstudio_url=source.get("FLEET_LMSTUDIO_URL"),
            artifact_roots=roots,
            http_allowlist=http_allowlist,
            allowed_kinds=allowed_kinds,
        ),
        node_id=node_id,
        epoch_guard=EpochGuard(state_dir / "highest-epoch.json"),
        poll_interval=float(source.get("FLEET_CONTINUITY_POLL_INTERVAL", "10")),
        claim_ttl_ms=int(source.get("FLEET_CONTINUITY_CLAIM_TTL_MS", "900000")),
        extra_capabilities=_csv(source.get("FLEET_CONTINUITY_CAPABILITIES")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded AssistX continuity node worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    agent = build_agent_from_env()
    if args.once:
        print(json.dumps(agent.run_once(), sort_keys=True))
        return 0
    return agent.loop()


if __name__ == "__main__":
    raise SystemExit(main())
