"""Audit-preserving RTK output compaction sidecar.

The wrapped command always executes exactly once. Raw stdout and stderr are
persisted before optional RTK filtering, and RTK failures fall back to raw output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

_VERSION_RE = re.compile(r"\brtk\s+(?P<version>\d+\.\d+\.\d+)\b")


@dataclass(frozen=True)
class RTKBinary:
    path: str
    version: str
    sha256: str


@dataclass(frozen=True)
class RTKEvidence:
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    duration_ms: int
    filter_name: str | None
    compaction_status: str
    raw_stdout_path: str
    raw_stderr_path: str
    compact_stdout_path: str
    metadata_path: str
    raw_stdout_sha256: str
    raw_stderr_sha256: str
    compact_stdout_sha256: str
    binary: RTKBinary | None

    @property
    def model_output_path(self) -> str:
        return self.compact_stdout_path


class RTKAdapter:
    def __init__(self, binary_path: str, *, expected_version: str | None = None,
                 expected_sha256: str | None = None, timeout_seconds: int = 30) -> None:
        self.binary_path = str(Path(binary_path).expanduser())
        self.expected_version = expected_version
        self.expected_sha256 = expected_sha256
        self.timeout_seconds = int(timeout_seconds)

    def verify_binary(self) -> RTKBinary:
        path = Path(self.binary_path)
        if not path.is_file():
            raise RuntimeError(f"RTK binary not found: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.expected_sha256 and digest != self.expected_sha256:
            raise RuntimeError("RTK binary SHA-256 does not match pinned digest")
        result = subprocess.run(
            [str(path), "--version"], text=True, capture_output=True,
            timeout=10, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"RTK --version failed with exit {result.returncode}")
        match = _VERSION_RE.search(result.stdout + "\n" + result.stderr)
        if not match:
            raise RuntimeError("Unable to parse RTK version")
        version = match.group("version")
        if self.expected_version and version != self.expected_version:
            raise RuntimeError(
                f"RTK version mismatch: expected {self.expected_version}, got {version}"
            )
        return RTKBinary(path=str(path), version=version, sha256=digest)

    def run_command(self, command: list[str], *, cwd: str, evidence_dir: str,
                    filter_name: str | None = None) -> RTKEvidence:
        if not command or not command[0]:
            raise ValueError("command argv is required")
        working_dir = Path(cwd).resolve()
        if not working_dir.is_dir():
            raise ValueError(f"cwd does not exist: {working_dir}")

        started = time.monotonic()
        try:
            result = subprocess.run(
                command, cwd=str(working_dir), capture_output=True,
                timeout=self.timeout_seconds, check=False,
            )
            exit_code = result.returncode
            raw_stdout = result.stdout
            raw_stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            raw_stdout = exc.stdout or b""
            raw_stderr = exc.stderr or b""
        duration_ms = int((time.monotonic() - started) * 1000)

        binary: RTKBinary | None = None
        compact = raw_stdout
        compaction_status = "raw"
        if filter_name:
            try:
                binary = self.verify_binary()
                filtered = subprocess.run(
                    [binary.path, "pipe", "--filter", filter_name],
                    input=raw_stdout, capture_output=True,
                    timeout=10, check=False,
                )
                if filtered.returncode == 0:
                    compact = filtered.stdout
                    compaction_status = "compacted"
                else:
                    compaction_status = f"fallback:rtk-exit-{filtered.returncode}"
            except (RuntimeError, subprocess.TimeoutExpired):
                compaction_status = "fallback:rtk-unavailable"

        return self._persist(
            tuple(command), str(working_dir), exit_code, duration_ms,
            filter_name, compaction_status, raw_stdout, raw_stderr, compact,
            evidence_dir, binary,
        )

    def _persist(self, command: tuple[str, ...], cwd: str, exit_code: int,
                 duration_ms: int, filter_name: str | None, compaction_status: str,
                 raw_stdout: bytes, raw_stderr: bytes, compact: bytes,
                 evidence_dir: str, binary: RTKBinary | None) -> RTKEvidence:
        root = Path(evidence_dir)
        root.mkdir(parents=True, exist_ok=True)
        prefix = root / f"rtk-{time.time_ns()}"
        raw_stdout_path = prefix.with_suffix(".stdout.raw")
        raw_stderr_path = prefix.with_suffix(".stderr.raw")
        compact_stdout_path = prefix.with_suffix(".stdout.compact")
        metadata_path = prefix.with_suffix(".json")
        raw_stdout_path.write_bytes(raw_stdout)
        raw_stderr_path.write_bytes(raw_stderr)
        compact_stdout_path.write_bytes(compact)
        record = RTKEvidence(
            command=command, cwd=cwd, exit_code=exit_code, duration_ms=duration_ms,
            filter_name=filter_name, compaction_status=compaction_status,
            raw_stdout_path=str(raw_stdout_path), raw_stderr_path=str(raw_stderr_path),
            compact_stdout_path=str(compact_stdout_path), metadata_path=str(metadata_path),
            raw_stdout_sha256=hashlib.sha256(raw_stdout).hexdigest(),
            raw_stderr_sha256=hashlib.sha256(raw_stderr).hexdigest(),
            compact_stdout_sha256=hashlib.sha256(compact).hexdigest(),
            binary=binary,
        )
        metadata_path.write_text(
            json.dumps(asdict(record), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        return record
