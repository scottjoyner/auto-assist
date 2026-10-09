from pathlib import Path
import subprocess


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
    # A real worktree and observed HEAD are required for a source-bound task.
    subprocess.run(["git","init","-b","main"],cwd=repo,capture_output=True,check=True)
    subprocess.run(["git","-c","user.name=Test","-c","user.email=test@example.org",
                    "add","service.py"],cwd=repo,capture_output=True,check=True)
    subprocess.run(["git","-c","user.name=Test","-c","user.email=test@example.org",
                    "commit","-m","seed"],cwd=repo,capture_output=True,check=True)
    commit=subprocess.run(["git","rev-parse","HEAD"],cwd=repo,capture_output=True,
                          text=True,check=True).stdout.strip()
    info = {
        "alias": "project",
        "path": str(repo),
        "name": "project",
        "commit": commit,
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
    bound = analysis["payload"]["source_binding"]
    assert bound["task_id"] == analysis["id"]
    assert bound["work_id"] == analysis["id"]
    assert bound["worktree_realpath"] == str(repo.resolve())
    assert bound["head_sha"] == commit
    assert mutation["status"] == "PROPOSED"
    assert mutation["requires_approval"] is True
    assert mutation["payload"]["execution_mode"] == "bounded_improvement_proposal"


def test_repo_generator_does_not_start_when_disabled(monkeypatch):
    monkeypatch.setattr(generator, "REPO_TASK_GENERATOR_ENABLED", False)
    monkeypatch.setattr(generator, "_started", False)

    generator.start_repo_task_generator()

    assert generator._started is False


def test_repo_generator_refuses_unbound_review_even_if_analysis_auto_ready(tmp_path,monkeypatch):
    repo=tmp_path/"not-a-worktree"
    repo.mkdir()
    source=repo/"service.py"
    source.write_text("def example():\n    return 'never approve unbound source' * 6\n")
    info={"alias":"bad","path":str(repo),"name":"bad","commit":"a"*40,
          "branch":"main","has_changes":False}
    monkeypatch.setattr(generator,"_get_repo_info",lambda *args:info)
    monkeypatch.setattr(generator,"_changed_paths",lambda *args:[source])
    monkeypatch.setattr(generator,"REPO_TASK_AUTO_READY",True)
    monkeypatch.setattr(generator,"CREATE_MUTATION_PROPOSALS",False)
    assert generator._create_tasks_for_repo(repo,max_per_repo=1)==[]
