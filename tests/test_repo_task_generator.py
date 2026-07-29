from pathlib import Path

import assistx.repo_task_generator as generator


def test_repo_proposals_are_deterministic_and_review_first(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    source = repo / "service.py"
    source.write_text(
        "def healthy(service_status: str) -> bool:\n"
        "    \"\"\"Return whether the reported service state is healthy.\"\"\"\n"
        "    return service_status == 'online'\n"
    )
    info = {
        "path": str(repo),
        "name": "project",
        "commit": "abc123",
        "branch": "main",
    }
    monkeypatch.setattr(generator, "_get_repo_info", lambda _: info)
    monkeypatch.setattr(generator, "_find_code_files", lambda _: [source])
    monkeypatch.setattr(generator, "REPO_TASK_AUTO_READY", False)

    first = generator._create_tasks_for_repo(Path(repo), max_per_repo=1)
    second = generator._create_tasks_for_repo(Path(repo), max_per_repo=1)

    assert first == second
    assert first[0]["id"].startswith("repo-proposal-")
    assert first[0]["status"] == "PROPOSED"
    assert first[0]["payload"]["requires_approval"] is True
    assert first[0]["payload"]["execution_mode"] == "analysis_only"


def test_repo_generator_does_not_start_when_disabled(monkeypatch):
    monkeypatch.setattr(generator, "REPO_TASK_GENERATOR_ENABLED", False)
    monkeypatch.setattr(generator, "_repo_task_thread", None)

    generator.start_repo_task_generator()

    assert generator._repo_task_thread is None
