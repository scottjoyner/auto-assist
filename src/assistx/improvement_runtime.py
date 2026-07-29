from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def prepare_repository(
    contract: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    environment = env if env is not None else os.environ
    try:
        roots = json.loads(
            environment.get("ASSISTX_REPOSITORY_ROOTS_JSON", "{}")
        )
    except json.JSONDecodeError:
        roots = {}
    repository = str(contract.get("repository") or "")
    configured = roots.get(repository) if isinstance(roots, dict) else None
    if not configured:
        return {
            "ok": False,
            "reason": "repository_root_not_configured",
            "repository": repository,
        }
    root = Path(str(configured)).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        return {
            "ok": False,
            "reason": "configured_repository_is_not_a_git_worktree",
            "repository": repository,
        }
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    if status["returncode"] != 0:
        return {
            "ok": False,
            "reason": "git_status_failed",
            "detail": status["stderr"][:240],
        }
    if contract.get("requires_clean_worktree", True) and status["stdout"].strip():
        return {
            "ok": False,
            "reason": "worktree_not_clean",
            "dirty_paths": _status_paths(status["stdout"]),
        }
    return {
        "ok": True,
        "root": str(root),
        "head": _run(["git", "rev-parse", "HEAD"], root)["stdout"].strip(),
        "clean_before": not status["stdout"].strip(),
    }


def collect_executor_evidence(
    contract: dict[str, Any],
    prepared: dict[str, Any],
    reported: dict[str, Any] | None,
) -> dict[str, Any]:
    if not prepared.get("ok"):
        return {
            "evidence_source": "executor",
            "worktree_clean_before": False,
            "changed_files": [],
            "diff_lines": 0,
            "tools_used": [],
            "verification": [],
            "summary": prepared.get("reason"),
            "executor_error": prepared.get("reason"),
        }
    root = Path(prepared["root"])
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    changed_files = _status_paths(status["stdout"])
    allowed = set(contract.get("allowed_paths") or [])
    scope_ok = bool(changed_files) and all(path in allowed for path in changed_files)
    diff_lines = _diff_line_count(root, changed_files)
    verification = []
    if status["returncode"] == 0 and scope_ok:
        timeout = max(
            10,
            min(
                int(os.getenv("ASSISTX_IMPROVEMENT_VERIFY_TIMEOUT_SECONDS", "120")),
                600,
            ),
        )
        for command in contract.get("verification_commands") or []:
            result = _run(command, root, timeout=timeout)
            verification.append(
                {
                    "command": command,
                    "returncode": result["returncode"],
                    "stdout": result["stdout"][-4000:],
                    "stderr": result["stderr"][-4000:],
                }
            )
    return {
        "evidence_source": "executor",
        "worktree_clean_before": bool(prepared.get("clean_before")),
        "head_before": prepared.get("head"),
        "changed_files": changed_files,
        "diff_lines": diff_lines,
        "tools_used": list((reported or {}).get("tools_used") or []),
        "verification": verification,
        "summary": str((reported or {}).get("summary") or ""),
        "next_candidate": (reported or {}).get("next_candidate"),
        "scope_validated": scope_ok,
    }


def _run(
    command: list[str],
    cwd: Path,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(part) for part in command],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 124, "stdout": "", "stderr": str(exc)}


def _status_paths(output: str) -> list[str]:
    paths = []
    for line in output.splitlines():
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value:
            paths.append(value.replace("\\", "/"))
    return sorted(set(paths))


def _diff_line_count(root: Path, changed_files: list[str]) -> int:
    tracked = _run(["git", "diff", "--numstat", "HEAD", "--"], root)
    total = 0
    counted = set()
    for line in tracked["stdout"].splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        counted.add(path)
        if added.isdigit():
            total += int(added)
        if deleted.isdigit():
            total += int(deleted)
    for relative in set(changed_files) - counted:
        path = root / relative
        if path.is_file() and not path.is_symlink():
            try:
                total += len(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                total += 1
    return total
