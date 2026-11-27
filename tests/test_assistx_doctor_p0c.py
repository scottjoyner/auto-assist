"""P0-C doctor canonicalization: fully offline contract tests.

No docker daemon, HTTP endpoint, SQLite DB, credential file, recovery-hold
file, or network mount is touched. Every input is a synthetic fixture created
under ``tmp_path`` and injected into the doctor's pure functions.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Override exists only so the same test can be pointed at a simulated pre-fix
# runner to prove the compose check is the behavior that changes.
DOCTOR_PATH = Path(os.getenv(
    "ASSISTX_DOCTOR_SOURCE",
    str(REPO_ROOT / "scripts" / "assistx_doctor.py"),
))


@pytest.fixture(scope="module")
def doctor():
    assert DOCTOR_PATH.is_file(), (
        "P0-C: versioned scripts/assistx_doctor.py is missing; the base tree "
        "has only the unversioned runner outside the repo"
    )
    spec = importlib.util.spec_from_file_location("assistx_doctor_p0c", DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stub_http(url, timeout=None):
    return True, "stub:200"


def test_versioned_doctor_has_no_hardcoded_stale_project_dirs(doctor):
    source = DOCTOR_PATH.read_text()
    for stale_dir in ("/home/scott/git/auto-assist", "/home/scott/git/auto-assign"):
        assert stale_dir not in source, f"stale hardcoded project dir remains: {stale_dir}"


def test_compose_check_flags_missing_registered_config(doctor, tmp_path):
    present = tmp_path / "docker-compose.yml"
    present.write_text("services: {}\n")
    missing = tmp_path / "compose.override.yml"  # deliberately never created
    assert not missing.exists()

    found = doctor.compose_findings({"demo-project": [str(present), str(missing)]})

    assert len(found) == 1
    assert found[0]["check"] == "compose:demo-project:config-files"
    assert found[0]["status"] == "fail"
    assert str(missing) in found[0]["detail"]


def test_compose_check_passes_when_all_registered_files_exist(doctor, tmp_path):
    files = []
    for name in ("docker-compose.yml", "compose.override.yml"):
        path = tmp_path / name
        path.write_text("services: {}\n")
        files.append(str(path))

    found = doctor.compose_findings({"demo-project": files})

    assert len(found) == 1
    assert found[0]["status"] == "healthy"


def test_compose_projects_from_synthetic_docker_labels(doctor, tmp_path):
    present = tmp_path / "docker-compose.yml"
    present.write_text("services: {}\n")
    missing = tmp_path / "compose.override.yml"
    rows = [
        {
            "com.docker.compose.project": "demo-project",
            "com.docker.compose.project.config_files": f"{present}:{missing}",
        },
        {
            "com.docker.compose.project": "unrelated",
            "com.docker.compose.project.config_files": "",
        },
    ]

    projects = doctor.compose_projects_from_labels(rows)

    assert set(projects["demo-project"]) == {str(present), str(missing)}
    assert projects.get("unrelated") == []
    found = doctor.compose_findings(projects)
    assert any(f["status"] == "fail" for f in found)


def test_findings_end_to_end_offline_reports_compose_finding(doctor, tmp_path):
    present = tmp_path / "docker-compose.yml"
    present.write_text("services: {}\n")
    missing = tmp_path / "compose.override.yml"
    manifest = tmp_path / "compose-manifest.json"
    manifest.write_text(
        json.dumps({"projects": {"demo-project": [str(present), str(missing)]}})
    )

    findings = doctor.findings(
        compose_manifest=manifest,
        http_probe=_stub_http,
        docker_env={},
        freshness_checks=[],
        held=(set(), ""),
    )

    compose = [f for f in findings if f["check"].startswith("compose:")]
    assert len(compose) == 1
    assert compose[0]["status"] == "fail"
    assert str(missing) in compose[0]["detail"]
    # docker_env={} was injected: no live container/env probe should run.
    assert all(not f["check"].startswith("env:") for f in findings)


def test_compose_project_filter_and_skip_semantics(doctor, tmp_path, monkeypatch):
    present = tmp_path / "docker-compose.yml"
    present.write_text("services: {}\n")
    missing = tmp_path / "compose.override.yml"
    manifest = tmp_path / "compose-manifest.json"
    manifest.write_text(
        json.dumps({"projects": {"demo-project": [str(present), str(missing)]}})
    )
    kwargs = dict(
        compose_manifest=manifest,
        http_probe=_stub_http,
        docker_env={},
        freshness_checks=[],
        held=(set(), ""),
    )

    # --compose-project for an unregistered project must fail, not silently pass.
    monkeypatch.setattr(doctor, "COMPOSE_PROJECT_FILTER", "not-registered")
    report = doctor.findings(**kwargs)
    selected = [f for f in report if f["check"].startswith("compose:")]
    assert [f["check"] for f in selected] == ["compose:not-registered:config-files"]
    assert selected[0]["status"] == "fail"

    # --skip-compose-check must emit no compose findings at all.
    monkeypatch.setattr(doctor, "SKIP_COMPOSE_CHECK", True)
    report = doctor.findings(**kwargs)
    assert not any(f["check"].startswith("compose:") for f in report)
