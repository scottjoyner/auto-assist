"""Bounded, non-authoritative Ripwire integration.

Ripwire is a read-only code-context sidecar. It never installs hooks, edits
files, schedules work, or changes AssistX authority.
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

_VERSION_RE = re.compile(r"\bripwire\s+(?P<version>\d+\.\d+\.\d+)\b")


@dataclass(frozen=True)
class RipwireBinary:
    path: str
    version: str
    sha256: str


@dataclass(frozen=True)
class RipwireEvidence:
    operation: str
    repository: str
    command: tuple[str, ...]
    exit_code: int
    duration_ms: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_path: str
    stderr_path: str
    metadata_path: str
    binary: RipwireBinary
    status: str
    payload: Any | None


class RipwireAdapter:
    """Run a narrow allowlist of read-only Ripwire lenses."""

    def __init__(self, binary_path: str, *, expected_version: str | None = None,
                 expected_sha256: str | None = None, timeout_seconds: int = 45) -> None:
        self.binary_path = str(Path(binary_path).expanduser())
        self.expected_version = expected_version
        self.expected_sha256 = expected_sha256
        self.timeout_seconds = int(timeout_seconds)

    def verify_binary(self) -> RipwireBinary:
        path = Path(self.binary_path)
        if not path.is_file():
            raise RuntimeError(f"Ripwire binary not found: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.expected_sha256 and digest != self.expected_sha256:
            raise RuntimeError("Ripwire binary SHA-256 does not match pinned digest")
        result = subprocess.run(
            [str(path), "--version"], text=True, capture_output=True,
            timeout=10, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Ripwire --version failed with exit {result.returncode}")
        match = _VERSION_RE.search(result.stdout + "\n" + result.stderr)
        if not match:
            raise RuntimeError("Unable to parse Ripwire version")
        version = match.group("version")
        if self.expected_version and version != self.expected_version:
            raise RuntimeError(
                f"Ripwire version mismatch: expected {self.expected_version}, got {version}"
            )
        return RipwireBinary(path=str(path), version=version, sha256=digest)

    def orient(self, repository: str, task: str, *, evidence_dir: str,
               max_tokens: int = 3500) -> RipwireEvidence:
        if not task.strip():
            raise ValueError("task is required")
        return self._run(
            "orient", repository,
            [f"--for={task}", "--json", f"--token-budget={int(max_tokens)}"],
            evidence_dir=evidence_dir, accepted_exit_codes={0},
        )
    def post_change(self, repository: str, *, evidence_dir: str,
                    changed: tuple[str, ...] = ()) -> RipwireEvidence:
        arg = "--situ" if not changed else f"--situ={','.join(changed)}"
        return self._run(
            "post_change", repository, [arg, "--json", "--token-budget=5000"],
            evidence_dir=evidence_dir, accepted_exit_codes={0},
        )

    def test_gate(self, repository: str, *, evidence_dir: str,
                  changed: tuple[str, ...] = ()) -> RipwireEvidence:
        arg = "--test-gate" if not changed else f"--test-gate={','.join(changed)}"
        return self._run(
            "test_gate", repository, [arg, "--json", "--token-budget=5000"],
            evidence_dir=evidence_dir, accepted_exit_codes={0, 4},
        )

    def _run(self, operation: str, repository: str, args: list[str], *,
             evidence_dir: str, accepted_exit_codes: set[int]) -> RipwireEvidence:
        repo = Path(repository).resolve()
        if not repo.is_dir():
            raise ValueError(f"repository does not exist: {repo}")
        binary = self.verify_binary()
        command = [binary.path, str(repo), *args]
        started = time.monotonic()
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=self.timeout_seconds, check=False,
            )
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
            status = "ok" if code in accepted_exit_codes else "error"
            if code == 4 and operation == "test_gate":
                status = "findings"
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            code, status = 124, "timeout"
        return self._persist(
            operation, repo, command, code,
            int((time.monotonic() - started) * 1000), stdout, stderr,
            evidence_dir, binary, status=status,
        )

    def _persist(self, operation: str, repo: Path, command: list[str],
                 exit_code: int, duration_ms: int, stdout: bytes, stderr: bytes,
                 evidence_dir: str, binary: RipwireBinary, *, status: str) -> RipwireEvidence:
        root = Path(evidence_dir)
        root.mkdir(parents=True, exist_ok=True)
        prefix = root / f"ripwire-{operation}-{time.time_ns()}"
        stdout_path = prefix.with_suffix(".stdout")
        stderr_path = prefix.with_suffix(".stderr")
        metadata_path = prefix.with_suffix(".json")
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        try:
            payload: Any | None = json.loads(stdout.decode("utf-8")) if stdout else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        record = RipwireEvidence(
            operation=operation, repository=str(repo), command=tuple(command),
            exit_code=exit_code, duration_ms=duration_ms,
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            metadata_path=str(metadata_path), binary=binary,
            status=status, payload=payload,
        )
        metadata_path.write_text(
            json.dumps(asdict(record), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        return record
