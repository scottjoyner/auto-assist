from pathlib import Path

import assistx.repo_task_generator as generator


def test_repo_work_is_deterministic_and_review_first(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    source = repo / "service.py"
    source.write_text(
        "def healthy(service_status: str) -> bool:\n"
        "    \"\"\"Return whether the reported service state is healthy.\"\"\"\n"
        "    return service_status == 'online'\n",
        encoding="utf-8",
    )
    info = {
        "alias": "project",
        "path": str(repo),
        "name": "project",
        "commit": "abc123",
        "branch": "main",
        "has_changes": False,
    }
    monkeypatch.setattr(generator, "_get_repo_info", lambda *args: info)
    monkeypatch.setattr(generator, "_changed_paths", lambda *args: [source])
    monkeypatch.setattr(generator, "REPO_TASK_AUTO_READY", False)

    first = generator._create_tasks_for_repo(Path(repo), max_per_repo=1)
    second = generator._create_tasks_for_repo(Path(repo), max_per_repo=1)

    assert first == second
    analysis = next(task for task in first if task["kind"].startswith("repo_") and task["kind"] != "repo_improvement_proposal")
    mutation = next(task for task in first if task["kind"] == "repo_improvement_proposal")
    assert analysis["id"].startswith("repo-analysis-")
    assert analysis["status"] == "PROPOSED"
    assert analysis["requires_approval"] is True
    assert analysis["payload"]["execution_mode"] == "analysis_only"
    assert mutation["status"] == "PROPOSED"
    assert mutation["requires_approval"] is True
    assert mutation["payload"]["execution_mode"] == "bounded_improvement_proposal"


def test_repo_generator_does_not_start_when_disabled(monkeypatch):
    monkeypatch.setattr(generator, "REPO_TASK_GENERATOR_ENABLED", False)
    monkeypatch.setattr(generator, "_started", False)

    generator.start_repo_task_generator()

    assert generator._started is False

def test_auto_ready_requires_configured_repository_source(tmp_path, monkeypatch):
    """No configured alias -> PROPOSED, never autonomous READY."""
    import json
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    source = repo / "service.py"
    source.write_text("def safe() -> bool:\n    return True\n" * 4)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-q",
                    "-m", "fixture"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          text=True, capture_output=True, check=True).stdout.strip()
    info = {"alias": "repo", "path": str(repo), "commit": head}
    monkeypatch.setattr(generator, "REPO_TASK_AUTO_READY", True)
    monkeypatch.delenv("ASSISTX_REPOSITORY_ROOTS_JSON", raising=False)
    proposal = generator._analysis_task(info, source, "code_analysis")
    assert proposal["status"] == "PROPOSED"
    assert proposal["requires_approval"] is True
    assert proposal["payload"]["source_binding"] is None

    monkeypatch.setenv("ASSISTX_REPOSITORY_ROOTS_JSON",
                       json.dumps({"repo": str(repo.resolve())}))
    ready = generator._analysis_task(info, source, "code_analysis")
    assert ready["status"] == "READY"
    assert ready["requires_approval"] is False
    assert ready["payload"]["source_binding"]["repo_realpath"] == str(repo.resolve())
    assert ready["payload"]["source_binding"]["task_id"] == ready["id"]
    assert ready["payload"]["source_binding"]["work_id"] == ready["id"]

    monkeypatch.setenv("ASSISTX_REPOSITORY_ROOTS_JSON",
                       json.dumps({"repo": str(tmp_path / "wrong")}))
    held = generator._analysis_task(info, source, "code_analysis")
    assert held["status"] == "PROPOSED"
    assert held["payload"]["source_binding"] is None
