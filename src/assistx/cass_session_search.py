"""Strictly read-only CASS session-search integration.

CASS is an observational index over agent histories. AssistX never treats the
CASS index as canonical state and this adapter never invokes indexing or repair.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

CASS_LICENSE = "MIT+OpenAI/Anthropic Rider"
_VERSION_RE = re.compile(r"\bcass\s+(?P<version>\d+\.\d+\.\d+)\b")


@dataclass(frozen=True)
class CassBinary:
    path: str
    version: str
    sha256: str
    selftest_status: str


@dataclass(frozen=True)
class CassSearchEvidence:
    query: str
    workspace: str
    command: tuple[str, ...]
    exit_code: int
    duration_ms: int
    status: str
    stdout_path: str
    stderr_path: str
    metadata_path: str
    stdout_sha256: str
    stderr_sha256: str
    payload: dict[str, Any] | None
    binary: CassBinary


class CassSessionSearch:
    def __init__(self, binary_path: str, *, expected_version: str | None = None,
                 expected_sha256: str | None = None, timeout_seconds: int = 15) -> None:
        self.binary_path = str(Path(binary_path).expanduser())
        self.expected_version = expected_version
        self.expected_sha256 = expected_sha256
        self.timeout_seconds = int(timeout_seconds)

    def verify_binary(self) -> CassBinary:
        path = Path(self.binary_path)
        if not path.is_file():
            raise RuntimeError(f"CASS binary not found: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.expected_sha256 and digest != self.expected_sha256:
            raise RuntimeError("CASS binary SHA-256 does not match pinned digest")
        version_result = subprocess.run(
            [str(path), "--version"], text=True, capture_output=True,
            timeout=10, check=False,
        )
        if version_result.returncode != 0:
            raise RuntimeError(f"CASS --version failed with exit {version_result.returncode}")
        match = _VERSION_RE.search(version_result.stdout + "\n" + version_result.stderr)
        if not match:
            raise RuntimeError("Unable to parse CASS version")
        version = match.group("version")
        if self.expected_version and version != self.expected_version:
            raise RuntimeError(
                f"CASS version mismatch: expected {self.expected_version}, got {version}"
            )
        selftest = subprocess.run(
            [str(path), "selftest", "--json"], text=True, capture_output=True,
            timeout=10, check=False,
        )
        if selftest.returncode != 0:
            raise RuntimeError(f"CASS selftest failed with exit {selftest.returncode}")
        try:
            payload = json.loads(selftest.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CASS selftest did not return JSON") from exc
        if payload.get("archive_accessed") is not False:
            raise RuntimeError("CASS selftest unexpectedly accessed the archive")
        if payload.get("functional") is not True:
            raise RuntimeError("CASS selftest did not report functional=true")
        return CassBinary(
            path=str(path), version=version, sha256=digest,
            selftest_status=str(payload.get("status") or "unknown"),
        )

    def search(self, query: str, *, workspace: str, evidence_dir: str,
               days: int = 7, limit: int = 5, max_tokens: int = 2000,
               query_timeout_ms: int = 2000) -> CassSearchEvidence:
        if not query.strip():
            raise ValueError("query is required")
        work = Path(workspace).resolve()
        if not work.is_dir():
            raise ValueError(f"workspace does not exist: {work}")
        binary = self.verify_binary()
        command = [
            binary.path, "search", query,
            "--workspace", str(work),
            "--days", str(int(days)),
            "--mode", "lexical",
            "--no-maintenance",
            "--json", "--robot-meta",
            "--fields", "minimal",
            "--limit", str(int(limit)),
            "--max-tokens", str(int(max_tokens)),
            "--timeout", str(int(query_timeout_ms)),
        ]
        return self._run_search(
            query=query, workspace=str(work), command=command,
            evidence_dir=evidence_dir, binary=binary,
        )

    def _run_search(self, *, query: str, workspace: str, command: list[str],
                    evidence_dir: str, binary: CassBinary) -> CassSearchEvidence:
        started = time.monotonic()
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=self.timeout_seconds, check=False,
            )
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
            status = "ok" if code == 0 else "error"
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            code, status = 124, "timeout"
        duration_ms = int((time.monotonic() - started) * 1000)
        payload: dict[str, Any] | None = None
        if stdout:
            try:
                parsed = json.loads(stdout.decode("utf-8"))
                payload = parsed if isinstance(parsed, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                status = "invalid-json"
        if payload:
            budget = payload.get("budget")
            if isinstance(budget, dict) and budget.get("timed_out") is True:
                status = "partial-timeout"
            if payload.get("status") == "maintenance-required":
                status = "maintenance-required"
        return self._persist(
            query, workspace, command, code, duration_ms, status,
            stdout, stderr, evidence_dir, payload, binary,
        )

    def _persist(self, query: str, workspace: str, command: list[str],
                 exit_code: int, duration_ms: int, status: str,
                 stdout: bytes, stderr: bytes, evidence_dir: str,
                 payload: dict[str, Any] | None, binary: CassBinary) -> CassSearchEvidence:
        root = Path(evidence_dir)
        root.mkdir(parents=True, exist_ok=True)
        prefix = root / f"cass-search-{time.time_ns()}"
        stdout_path = prefix.with_suffix(".stdout")
        stderr_path = prefix.with_suffix(".stderr")
        metadata_path = prefix.with_suffix(".json")
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        record = CassSearchEvidence(
            query=query, workspace=workspace, command=tuple(command),
            exit_code=exit_code, duration_ms=duration_ms, status=status,
            stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            metadata_path=str(metadata_path),
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            payload=payload, binary=binary,
        )
        metadata_path.write_text(
            json.dumps(asdict(record), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        return record
