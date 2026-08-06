"""Durable repository work-supply controller.

Repository scanning is configuration-driven and refreshes Git state on every
cycle. Safe read-only analysis can enter the background READY queue; code
mutation remains a separate PROPOSED, approval-required contract.

Exactly one scanner leader runs fleet-wide through ``DurableController``.
Repository scan cursors are persisted in Neo4j so new commits produce new work
without repeatedly re-analyzing an unchanged tree.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .controller_runtime import (
    DurableController,
    Neo4jControllerStore,
    start_durable_controller_loop,
)
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

REPO_TASK_INTERVAL = max(30, int(os.getenv("REPO_TASK_INTERVAL", "300")))
MAX_TASKS_PER_CYCLE = max(
    1, min(200, int(os.getenv("REPO_MAX_TASKS_PER_CYCLE", "20")))
)
MAX_FILES_PER_REPO = max(
    1, min(50, int(os.getenv("REPO_MAX_FILES_PER_REPO", "5")))
)
MAX_FILE_BYTES = max(
    4096, min(1_000_000, int(os.getenv("REPO_MAX_FILE_BYTES", "500000")))
)
MAX_PROMPT_CHARS = max(
    1000, min(50_000, int(os.getenv("REPO_MAX_PROMPT_CHARS", "12000")))
)
REPO_TASK_GENERATOR_ENABLED = os.getenv(
    "ASSISTX_REPO_TASK_GENERATOR_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
REPO_TASK_AUTO_READY = os.getenv(
    "ASSISTX_REPO_TASK_AUTO_READY_ANALYSIS",
    os.getenv("ASSISTX_REPO_TASK_AUTO_READY", "true"),
).lower() in {"1", "true", "yes", "on"}
CREATE_MUTATION_PROPOSALS = os.getenv(
    "ASSISTX_REPO_CREATE_MUTATION_PROPOSALS", "true"
).lower() in {"1", "true", "yes", "on"}

TASK_KINDS = [
    "code_analysis",
    "doc_generation",
    "refactor_suggestion",
    "test_gap_analysis",
    "security_audit",
    "dependency_audit",
    "performance_review",
    "architecture_review",
]

INCLUDE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".cpp",
    ".h",
    ".c",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".kt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".sql",
    ".md",
    ".rst",
}
INCLUDE_NAMES = {"Dockerfile", "Makefile", "Containerfile"}
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".cache",
    "target",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}

PROMPTS = {
    "code_analysis": "Analyze purpose, data flow, edge cases, and concrete defects.",
    "doc_generation": "Identify missing or stale documentation and draft precise improvements.",
    "refactor_suggestion": "Identify bounded refactors, risks, and verification needed.",
    "test_gap_analysis": "Identify untested behavior and specify high-value regression tests.",
    "security_audit": "Audit trust boundaries, input handling, credentials, and unsafe execution.",
    "dependency_audit": "Audit imports, version risks, coupling, and deprecated dependencies.",
    "performance_review": "Audit blocking I/O, database access, concurrency, and memory risks.",
    "architecture_review": "Audit ownership, state authority, failure handling, and observability.",
}

_started = False
_start_lock = threading.Lock()
_stop_event = threading.Event()


def _repository_map() -> dict[str, Path]:
    """Resolve the canonical repository alias map from configuration."""

    raw = os.getenv("ASSISTX_REPOSITORY_ROOTS_JSON", "").strip()
    if not raw:
        return {}
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("repo task generator: invalid repository map JSON: %s", exc)
        return {}
    if not isinstance(document, dict):
        logger.error("repo task generator: repository map must be an object")
        return {}

    result: dict[str, Path] = {}
    for alias, value in document.items():
        path_value: Any = value
        if isinstance(value, dict):
            path_value = value.get("path") or value.get("root")
        path = Path(str(path_value or "")).expanduser()
        if not str(alias).strip() or not str(path_value or "").strip():
            continue
        result[str(alias).strip()] = path.resolve()
    return result


# Compatibility view. It is intentionally configuration-derived, not hard-coded.
REPO_ROOTS = [str(path) for path in _repository_map().values()]


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 30,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        shell=False,
    )


def _git_text(args: list[str], cwd: Path, timeout: int = 30) -> str | None:
    try:
        result = _run_git(args, cwd, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _get_repo_info(repo_path: Path, alias: str | None = None) -> dict[str, Any] | None:
    """Read fresh repository/worktree state; no permanent metadata cache."""

    if not repo_path.exists() or not repo_path.is_dir():
        return None
    root = _git_text(["rev-parse", "--show-toplevel"], repo_path)
    head = _git_text(["rev-parse", "HEAD"], repo_path)
    if not root or not head:
        return None
    root_path = Path(root).resolve()
    branch = _git_text(["rev-parse", "--abbrev-ref", "HEAD"], root_path) or "DETACHED"
    origin = _git_text(["config", "--get", "remote.origin.url"], root_path) or ""
    dirty = bool(_git_text(["status", "--porcelain"], root_path) or "")
    return {
        "alias": alias or root_path.name,
        "path": str(root_path),
        "name": root_path.name,
        "url": origin,
        "branch": branch,
        "commit": head,
        "has_changes": dirty,
        "is_worktree": (root_path / ".git").is_file(),
    }


def _allowed_file(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.name in INCLUDE_NAMES or path.suffix.lower() in INCLUDE_SUFFIXES:
        try:
            return path.is_file() and path.stat().st_size <= MAX_FILE_BYTES
        except OSError:
            return False
    return False


def _changed_paths(repo: Path, previous: str | None, head: str) -> list[Path]:
    names: list[str] = []
    if previous and previous != head:
        try:
            ancestor = _run_git(["merge-base", "--is-ancestor", previous, head], repo)
        except (OSError, subprocess.SubprocessError):
            ancestor = None
        if ancestor is not None and ancestor.returncode == 0:
            output = _git_text(
                ["diff", "--name-only", "--diff-filter=ACMRT", f"{previous}..{head}"],
                repo,
                timeout=60,
            )
            names = output.splitlines() if output else []
    if not names:
        output = _git_text(["ls-files"], repo, timeout=60)
        names = output.splitlines() if output else []

    result: list[Path] = []
    for name in names:
        relative = Path(name)
        candidate = (repo / relative).resolve()
        try:
            candidate.relative_to(repo.resolve())
        except ValueError:
            continue
        if _allowed_file(relative) and _allowed_file(candidate):
            result.append(candidate)
    result.sort(
        key=lambda path: (
            -path.stat().st_mtime if path.exists() else 0,
            str(path.relative_to(repo)),
        )
    )
    return result


def _find_code_files(repo_path: Path) -> list[Path]:
    info = _get_repo_info(repo_path)
    if not info:
        return []
    return _changed_paths(Path(info["path"]), None, str(info["commit"]))


def _detect_language(file_path: Path) -> str:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cpp": "cpp",
        ".h": "cpp",
        ".c": "c",
        ".cs": "csharp",
        ".php": "php",
        ".rb": "ruby",
        ".swift": "swift",
        ".kt": "kotlin",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".sh": "bash",
        ".sql": "sql",
        ".md": "markdown",
        ".rst": "rst",
    }
    return mapping.get(file_path.suffix.lower(), "text")


def _selector(alias: str, relative_path: str, commit: str) -> str:
    digest = hashlib.sha256(
        f"{alias}:{relative_path}:{commit}".encode("utf-8")
    ).digest()
    return TASK_KINDS[digest[0] % len(TASK_KINDS)]


def _create_task_payload(
    kind: str,
    repo_info: dict[str, Any],
    file_path: Path,
    code: str,
) -> dict[str, Any]:
    repo = Path(str(repo_info["path"]))
    relative = str(file_path.relative_to(repo))
    language = _detect_language(file_path)
    instruction = PROMPTS[kind]
    prompt = (
        "You are performing read-only repository analysis. Do not claim that a "
        "change was applied. Produce findings, exact evidence, and a bounded next "
        "action that can become an approval-gated improvement contract.\n\n"
        f"Repository: {repo_info['alias']}\n"
        f"Commit: {repo_info['commit']}\n"
        f"File: {relative}\n"
        f"Language: {language}\n"
        f"Review objective: {instruction}\n\n"
        f"```{language}\n{code[:MAX_PROMPT_CHARS]}\n```"
    )
    return {
        "kind": f"repo_{kind}",
        "repository": repo_info["alias"],
        "repository_path": repo_info["path"],
        "source_commit": repo_info["commit"],
        "file": relative,
        "language": language,
        "prompt": prompt,
        "model": "",
        "harvester": "repo_task_generator",
        "execution_mode": "analysis_only",
        "requires_approval": False,
    }


def _analysis_task(
    repo_info: dict[str, Any], file_path: Path, kind: str
) -> dict[str, Any] | None:
    try:
        code = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if len(code.strip()) < 50:
        return None
    relative = str(file_path.relative_to(Path(str(repo_info["path"]))))
    identity = hashlib.sha256(
        f"{repo_info['alias']}:{repo_info['commit']}:{relative}:{kind}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    payload = _create_task_payload(kind, repo_info, file_path, code)
    return {
        "id": f"repo-analysis-{identity}",
        "title": f"[{kind}] {repo_info['alias']}: {relative}",
        "kind": f"repo_{kind}",
        "status": "READY" if REPO_TASK_AUTO_READY else "PROPOSED",
        "priority": "BACKGROUND",
        "required_capabilities": ["llm"],
        "requires_approval": not REPO_TASK_AUTO_READY,
        "payload": payload,
    }


def _mutation_proposal(
    repo_info: dict[str, Any], changed_paths: list[Path]
) -> dict[str, Any] | None:
    if not CREATE_MUTATION_PROPOSALS or not changed_paths:
        return None
    relative_paths = [
        str(path.relative_to(Path(str(repo_info["path"]))))
        for path in changed_paths[:10]
    ]
    identity = hashlib.sha256(
        f"{repo_info['alias']}:{repo_info['commit']}:mutation-proposal".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return {
        "id": f"repo-improvement-proposal-{identity}",
        "title": f"Review improvement candidates for {repo_info['alias']}@{str(repo_info['commit'])[:12]}",
        "kind": "repo_improvement_proposal",
        "status": "PROPOSED",
        "priority": "LOW",
        "required_capabilities": ["llm", "code"],
        "requires_approval": True,
        "payload": {
            "repository": repo_info["alias"],
            "repository_path": repo_info["path"],
            "source_commit": repo_info["commit"],
            "allowed_paths": relative_paths,
            "execution_mode": "bounded_improvement_proposal",
            "requires_approval": True,
            "prompt": (
                "Synthesize the read-only findings for these changed files into a "
                "bounded improvement proposal. Do not edit files, commit, push, or "
                "open a pull request. Specify exact allowed paths and verification.\n\n"
                + "\n".join(f"- {path}" for path in relative_paths)
            ),
        },
    }


def _create_tasks_for_repo(
    repo_path: Path,
    max_per_repo: int = 5,
    *,
    alias: str | None = None,
    previous_commit: str | None = None,
) -> list[dict[str, Any]]:
    info = _get_repo_info(repo_path, alias)
    if not info:
        return []
    root = Path(str(info["path"]))
    changed = _changed_paths(root, previous_commit, str(info["commit"]))
    tasks: list[dict[str, Any]] = []
    for file_path in changed[: max(1, max_per_repo)]:
        relative = str(file_path.relative_to(root))
        kind = _selector(str(info["alias"]), relative, str(info["commit"]))
        task = _analysis_task(info, file_path, kind)
        if task:
            tasks.append(task)
    proposal = _mutation_proposal(info, changed)
    if proposal:
        tasks.append(proposal)
    return tasks


def _cursor_map(neo: Any) -> dict[str, str]:
    with neo._session() as session:
        rows = session.run(
            """
            MATCH (c:RepositoryScanCursor)
            RETURN c.repository AS repository, c.head_commit AS head_commit
            """
        )
        return {
            str(row["repository"]): str(row["head_commit"])
            for row in rows
            if row.get("repository") and row.get("head_commit")
        }


def create_repo_tasks(max_total: int = 20) -> list[dict[str, Any]]:
    """Compatibility entry point using current configured roots and cursors."""

    neo = Neo4jClient()
    try:
        cursors = _cursor_map(neo)
    finally:
        neo.close()
    result: list[dict[str, Any]] = []
    repositories = _repository_map()
    if not repositories:
        return []
    per_repo = max(1, min(MAX_FILES_PER_REPO, max_total // len(repositories) or 1))
    for alias, path in repositories.items():
        result.extend(
            _create_tasks_for_repo(
                path,
                per_repo,
                alias=alias,
                previous_commit=cursors.get(alias),
            )
        )
        if len(result) >= max_total:
            break
    return result[:max_total]


def _persist_tasks(
    tasks: list[dict[str, Any]],
    *,
    neo: Any | None = None,
    cursor_updates: dict[str, dict[str, Any]] | None = None,
) -> int:
    if not tasks and not cursor_updates:
        return 0
    owned = neo is None
    client = neo or Neo4jClient()
    created = 0
    try:
        with client._session() as session:
            for task in tasks:
                row = session.run(
                    """
                    MERGE (t:Task {id:$id})
                    ON CREATE SET t.title=$title,
                                  t.kind=$kind,
                                  t.status=$status,
                                  t.priority=$priority,
                                  t.required_capabilities=$capabilities,
                                  t.requires_approval=$requires_approval,
                                  t.payload_json=$payload_json,
                                  t.created_at=datetime(),
                                  t.created_at_ts=timestamp(),
                                  t.updated_at=datetime(),
                                  t.updated_at_ts=timestamp(),
                                  t.created_by='repo-task-generator',
                                  t.repo_generator_created=true
                    RETURN t.repo_generator_created AS generator_created,
                           t.created_at_ts AS created_at_ts
                    """,
                    {
                        "id": task["id"],
                        "title": task["title"],
                        "kind": task["kind"],
                        "status": task["status"],
                        "priority": task["priority"],
                        "capabilities": task["required_capabilities"],
                        "requires_approval": bool(task["requires_approval"]),
                        "payload_json": json.dumps(task["payload"], sort_keys=True),
                    },
                ).single()
                if row and row.get("generator_created"):
                    created += 1
            for alias, update in (cursor_updates or {}).items():
                session.run(
                    """
                    MERGE (c:RepositoryScanCursor {repository:$repository})
                    SET c.head_commit=$head_commit,
                        c.repository_path=$repository_path,
                        c.branch=$branch,
                        c.dirty=$dirty,
                        c.scanned_at_ts=timestamp(),
                        c.updated_at_ts=timestamp()
                    """,
                    {
                        "repository": alias,
                        "head_commit": update["commit"],
                        "repository_path": update["path"],
                        "branch": update["branch"],
                        "dirty": bool(update["has_changes"]),
                    },
                ).consume()
        return created
    finally:
        if owned:
            client.close()


def repo_task_cycle() -> dict[str, Any]:
    if _stop_event.is_set():
        return {"created": 0, "reason": "stopping"}
    repositories = _repository_map()
    if not repositories:
        return {"created": 0, "reason": "no_configured_repositories"}
    neo = Neo4jClient()
    try:
        cursors = _cursor_map(neo)
        tasks: list[dict[str, Any]] = []
        updates: dict[str, dict[str, Any]] = {}
        per_repo = max(
            1,
            min(
                MAX_FILES_PER_REPO,
                MAX_TASKS_PER_CYCLE // len(repositories) or 1,
            ),
        )
        for alias, path in repositories.items():
            info = _get_repo_info(path, alias)
            if not info:
                logger.warning(
                    "repo task generator: %s is unavailable or not a Git worktree",
                    alias,
                )
                continue
            updates[alias] = info
            tasks.extend(
                _create_tasks_for_repo(
                    Path(str(info["path"])),
                    per_repo,
                    alias=alias,
                    previous_commit=cursors.get(alias),
                )
            )
            if len(tasks) >= MAX_TASKS_PER_CYCLE:
                break
        tasks = tasks[:MAX_TASKS_PER_CYCLE]
        created = _persist_tasks(tasks, neo=neo, cursor_updates=updates)
        return {
            "created": created,
            "generated": len(tasks),
            "repositories": len(updates),
            "configured_repositories": len(repositories),
        }
    finally:
        neo.close()


def start_repo_task_generator() -> None:
    global _started
    if not REPO_TASK_GENERATOR_ENABLED:
        logger.info("repo task generator: disabled")
        return
    with _start_lock:
        if _started:
            return
        _started = True
        _stop_event.clear()

        def store_factory() -> tuple[Neo4jControllerStore, Any]:
            neo = Neo4jClient()
            return Neo4jControllerStore(neo), neo.close

        controller = DurableController(
            "repo-task-generator",
            store_factory,
            lease_seconds=max(90, REPO_TASK_INTERVAL * 3),
        )
        start_durable_controller_loop(
            controller,
            repo_task_cycle,
            interval_seconds=REPO_TASK_INTERVAL,
        )
        logger.info("repo task generator: durable loop started")


def stop_repo_task_generator() -> None:
    _stop_event.set()


def trigger_repo_tasks_once(max_tasks: int = 20) -> int:
    tasks = create_repo_tasks(max_total=max_tasks)
    return _persist_tasks(tasks)
