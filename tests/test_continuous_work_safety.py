from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from assistx import repo_task_generator, work_supply
from assistx.neo4j_client import Neo4jClient


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return result.stdout.strip()


def commit_file(repo: Path, relative: str, content: str, message: str) -> str:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(repo, "add", relative)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def test_claimless_worker_mutations_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", "true")
    monkeypatch.delenv("ASSISTX_LEGACY_CLAIMLESS_AGENTS", raising=False)
    neo = Neo4jClient.__new__(Neo4jClient)

    assert (
        neo.heartbeat_task("task-1", "worker-a", status="RUNNING", claim_id=None)
        is None
    )
    assert (
        neo.complete_task("task-1", "worker-a", "DONE", claim_id=None)
        is None
    )


class DummyNeo:
    def close(self) -> None:
        return None


class DummyFactory:
    def __call__(self) -> DummyNeo:
        return DummyNeo()


def test_work_supply_yields_to_priority_queue(monkeypatch) -> None:
    monkeypatch.setattr(work_supply, "_projection_capacity", lambda factory: (9, 4))
    monkeypatch.setattr(work_supply, "_task_pressure", lambda neo: (1, 2, 0))

    decision = work_supply.compute_work_supply_decision(DummyFactory())

    assert decision.total_slots == 4
    assert decision.free_slots == 3
    assert decision.allow_background is False
    assert decision.reason == "priority_work_waiting"


def test_work_supply_targets_only_free_capacity(monkeypatch) -> None:
    monkeypatch.setattr(work_supply, "_projection_capacity", lambda factory: (9, 5))
    monkeypatch.setattr(work_supply, "_task_pressure", lambda neo: (3, 0, 1))
    monkeypatch.setattr(work_supply, "BACKGROUND_BACKLOG_PER_FREE_SLOT", 3)

    decision = work_supply.compute_work_supply_decision(DummyFactory())

    assert decision.free_slots == 2
    assert decision.target_background_ready == 6
    assert decision.allow_background is True
    assert decision.reason == "idle_capacity_available"


def test_repository_scan_refreshes_head_and_skips_unchanged_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "sample"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    first = commit_file(
        repo,
        "src/example.py",
        "def first() -> str:\n    return 'first'\n",
        "first",
    )
    monkeypatch.setenv(
        "ASSISTX_REPOSITORY_ROOTS_JSON",
        json.dumps({"sample": str(repo)}),
    )

    initial = repo_task_generator._create_tasks_for_repo(
        repo,
        max_per_repo=5,
        alias="sample",
        previous_commit=None,
    )
    unchanged = repo_task_generator._create_tasks_for_repo(
        repo,
        max_per_repo=5,
        alias="sample",
        previous_commit=first,
    )
    second = commit_file(
        repo,
        "src/example.py",
        "def first() -> str:\n    return 'second'\n",
        "second",
    )
    refreshed = repo_task_generator._create_tasks_for_repo(
        repo,
        max_per_repo=5,
        alias="sample",
        previous_commit=first,
    )

    assert initial
    assert unchanged == []
    assert second != first
    assert refreshed
    assert all(task["payload"]["source_commit"] == second for task in refreshed)
    analysis = [task for task in refreshed if task["status"] == "READY"]
    proposals = [task for task in refreshed if task["status"] == "PROPOSED"]
    assert analysis
    assert proposals
    assert all(task["payload"]["execution_mode"] == "analysis_only" for task in analysis)
    assert all(task["requires_approval"] is True for task in proposals)


def test_repository_map_has_no_source_controlled_machine_paths(monkeypatch) -> None:
    monkeypatch.delenv("ASSISTX_REPOSITORY_ROOTS_JSON", raising=False)

    assert repo_task_generator._repository_map() == {}
    assert not any("/home/scott" in value for value in repo_task_generator.REPO_ROOTS)
    assert not any("/media/scott" in value for value in repo_task_generator.REPO_ROOTS)
