#!/usr/bin/env python3
"""Opt-in, offline, source-only Ripwire context navigation for research agents.

Does not install hooks, start an MCP server, contact LLMs, alter a source
checkout, grant repo authority, or classify signed SensorGraft evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile

VERSION = "v0.6.9"
RELEASE_SHA256 = (
    "5444af4124bec689c36a65879b44389dd18fda9fd4ad2037369fc6d27625080a"
)
BINARY_SHA256 = (
    "d72d639b298a01316fbd967af8156b8787c2cbe03ae6f0a91b10d8b9752abec2"
)
IMAGE = "python:3.12-slim-bookworm"
SOURCE_PARTS = ("src", "research", "tests", "contracts")
SOURCE_SUFFIXES = frozenset({".py", ".rs", ".sh", ".toml", ".md", ".js", ".ts"})
SKIP_DIRS = frozenset({
    ".git", ".cache", ".venv", ".pytest_cache", "node_modules", "target",
    "__pycache__", ".next", ".tgrep", ".ripwire",
})
FILE_MAX_BYTES = 200_000
CORPUS_MAX_BYTES = 25 * 1024 * 1024
CORPUS_MAX_FILES = 3_000
OUTPUT_MAX_BYTES = 96 * 1024
BUDGET_TOKENS = 2_400


class ContextShadowError(ValueError):
    """Fail closed without exposing source code or diagnostic payloads."""


def no_symlink_ancestors(path: Path) -> bool:
    return path.is_absolute() and all(
        not part.is_symlink() for part in (path, *path.parents)
    )


def verify_binary(path: Path, *, expected_sha256: str = BINARY_SHA256) -> None:
    if not no_symlink_ancestors(path):
        raise ContextShadowError("binary path is not a canonical local file")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size < 75_000_000:
                raise ContextShadowError("invalid binary file")
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected_sha256:
                raise ContextShadowError("binary is not the pinned release")
        finally:
            os.close(fd)
    except OSError as exc:
        raise ContextShadowError("binary not readable") from exc


def stage_source_only(root: Path, target: Path) -> dict:
    """Bounded allowlisted copy; never stage raw traces, JSONL, media or keys."""
    if not no_symlink_ancestors(root) or not root.is_dir():
        raise ContextShadowError("source root must be an actual directory")
    if not target.is_dir():
        raise ContextShadowError("snapshot target missing")
    os.chmod(target, 0o755)
    count, total, skipped = 0, 0, 0
    for base in SOURCE_PARTS:
        subroot = root / base
        if not subroot.is_dir() or subroot.is_symlink():
            continue
        for current, dirs, filenames in os.walk(subroot, followlinks=False):
            dirs[:] = [
                name for name in sorted(dirs)
                if name not in SKIP_DIRS and not name.startswith(".")
                and not (Path(current) / name).is_symlink()
            ]
            for name in sorted(filenames):
                source = Path(current) / name
                if (
                    name.startswith(".") or source.suffix not in SOURCE_SUFFIXES
                    or source.is_symlink()
                ):
                    skipped += 1
                    continue
                try:
                    fd = os.open(
                        source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    )
                except OSError:
                    skipped += 1
                    continue
                try:
                    st = os.fstat(fd)
                    if (
                        not stat.S_ISREG(st.st_mode)
                        or st.st_size > FILE_MAX_BYTES
                        or total + st.st_size > CORPUS_MAX_BYTES
                        or count >= CORPUS_MAX_FILES
                    ):
                        skipped += 1
                        continue
                    dest = target / source.relative_to(root)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with os.fdopen(fd, "rb", closefd=False) as src:
                        payload = src.read(FILE_MAX_BYTES + 1)
                    now = os.fstat(fd)
                    if (
                        len(payload) != st.st_size
                        or now.st_size != st.st_size
                        or now.st_ino != st.st_ino
                        or now.st_mtime_ns != st.st_mtime_ns
                    ):
                        raise ContextShadowError("source changed during snapshot")
                    with dest.open("wb") as dst:
                        dst.write(payload)
                    os.chmod(dest, 0o644)
                    count += 1
                    total += st.st_size
                finally:
                    os.close(fd)
    if count == 0:
        raise ContextShadowError("no permitted source files to examine")
    return {"files": count, "bytes": total, "excluded": skipped}


def _json_pairs_unique(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for name, value in pairs:
        if name in result:
            raise ContextShadowError("duplicate untrusted JSON key")
        result[name] = value
    return result


def _safe_relative_path(raw: object) -> str:
    if type(raw) is not str or not 1 <= len(raw) <= 280:
        raise ContextShadowError("untrusted filename")
    if "\\" in raw or "\x00" in raw or any(ord(ch) < 32 for ch in raw):
        raise ContextShadowError("untrusted filename syntax")
    p = PurePosixPath(raw)
    if (
        p.is_absolute() or ".." in p.parts or "." in p.parts
        or len(p.parts) < 2 or p.parts[0] not in SOURCE_PARTS
        or any(part.startswith(".") for part in p.parts)
        or p.suffix not in SOURCE_SUFFIXES
    ):
        raise ContextShadowError("untrusted path outside source-only scope")
    return str(p)


def audit_ripwire_json(
    payload: bytes, *, task: str, snapshot: Path,
    budget: int = BUDGET_TOKENS,
) -> dict:
    if type(payload) is not bytes or not 2 <= len(payload) <= OUTPUT_MAX_BYTES:
        raise ContextShadowError("missing or unbounded map output")
    try:
        data = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_json_pairs_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ContextShadowError("invalid numeric map field")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextShadowError("invalid map JSON") from exc
    if type(data) is not dict or data.get("task") != task:
        raise ContextShadowError("map does not match the requested task")
    confidence = data.get("confidence")
    if type(confidence) is not str or confidence not in {
        "low", "medium", "high"
    }:
        raise ContextShadowError("missing or unrecognized confidence")
    estimate = data.get("est_tokens")
    reported_budget = data.get("budget_tokens")
    if (
        type(estimate) is not int or not 0 <= estimate <= 500_000
        or type(reported_budget) is not int or reported_budget != budget
        or type(data.get("capped")) is not bool
    ):
        raise ContextShadowError("untrusted context estimate")
    ranked = data.get("sigs")
    if type(ranked) is not list or len(ranked) > 128:
        raise ContextShadowError("invalid ranked symbols")
    seen_ranks: set[int] = set()
    found: dict[str, int] = {}
    for entry in ranked:
        if type(entry) is not dict:
            raise ContextShadowError("malformed ranked symbol")
        rank = entry.get("r")
        if type(rank) is not int or not 1 <= rank <= 100_000:
            raise ContextShadowError("malformed symbol rank")
        if rank in seen_ranks:
            raise ContextShadowError("ambiguous rank")
        seen_ranks.add(rank)
        path = _safe_relative_path(entry.get("p"))
        source = snapshot / path
        if source.is_symlink() or not source.is_file():
            raise ContextShadowError("ranked symbol outside source snapshot")
        found[path] = min(found.get(path, rank), rank)
    top = sorted(found.items(), key=lambda z: (z[1], z[0]))[:12]
    return {
        "kind": "ripwire_source_only_shadow_v1",
        "pinned_version": VERSION,
        "pinned_binary_sha256": BINARY_SHA256,
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "confidence_reported_by_tool": confidence,
        "estimated_tokens_reported_by_tool": estimate,
        "requested_soft_budget": budget,
        "reported_budget_exceeded": estimate > budget,
        "candidate_files": [
            {"relative_file": name, "first_symbol_rank": rank}
            for name, rank in top
        ],
        "code_bodies_exported": False,
        "signatures_exported": False,
        "model_tokens_used": 0,
        "model_quality_verified": False,
        "source_custody_verified": False,
        "independent_relevance_verified": False,
        "network_egress_allowed": False,
        "agent_hooks_installed": False,
        "production_gate": "HOLD",
    }


def docker_args(binary: Path, snapshot: Path, task: str) -> list[str]:
    if type(task) is not str or not 8 <= len(task) <= 240:
        raise ContextShadowError("invalid or unbounded task")
    if any(ord(c) < 32 or ord(c) == 127 for c in task):
        raise ContextShadowError("control character in task")
    if not no_symlink_ancestors(binary) or not no_symlink_ancestors(snapshot):
        raise ContextShadowError("invalid sandbox source paths")
    return [
        "docker", "run", "--rm", "--pull=never", "--network=none",
        "--read-only", "--user", "65534:65534", "--memory=750m",
        "--cpus=1", "--pids-limit=70", "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,nosuid,size=128m,mode=1777",
        "-v", f"{binary}:/usr/local/bin/ripwire:ro",
        "-v", f"{snapshot}:/corpus:ro", "-e", "HOME=/tmp",
        "-w", "/corpus", IMAGE,
        "timeout", "-k", "2s", "18s", "/usr/local/bin/ripwire",
        "/corpus", "--no-cache", "--json", "--signatures-only",
        f"--token-budget={BUDGET_TOKENS}", "--max-memory=256M",
        f"--for={task}",
    ]


def run_shadow(source: Path, binary: Path, task: str) -> dict:
    verify_binary(binary)
    with tempfile.TemporaryDirectory(prefix="ripwire-offline-shadow-") as tmp:
        snap = Path(tmp) / "code"
        snap.mkdir(mode=0o755)
        overview = stage_source_only(source, snap)
        command = docker_args(binary, snap, task)
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContextShadowError("sandbox unavailable or timed out") from exc
        if result.returncode != 0:
            raise ContextShadowError("offline code map failed")
        report = audit_ripwire_json(
            result.stdout, task=task, snapshot=snap
        )
        report["source_snapshot"] = overview
        return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--ripwire-binary", type=Path, required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--allow-code-read", action="store_true")
    a = p.parse_args(argv)
    if not a.allow_code_read:
        print("source read refused without --allow-code-read", file=sys.stderr)
        return 2
    try:
        report = run_shadow(a.source_root, a.ripwire_binary, a.task)
    except (ContextShadowError, ValueError):
        print("offline Ripwire shadow rejected; no services changed", file=sys.stderr)
        return 3
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
