"""Offline acceptance of guarded disposable Redis restart runner.

No Docker action may be launched by these tests.
"""
from __future__ import annotations

import importlib.util
import pathlib
import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/trace_redis_boot_disposable_probe.py"
spec = importlib.util.spec_from_file_location("trace_redis_disposable_probe", SCRIPT)
assert spec is not None and spec.loader is not None
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def test_without_explicit_approval_never_executes_docker(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Docker operation attempted without opt-in")
    monkeypatch.setattr(subject.subprocess, "run", forbidden)
    assert subject.main([
        "--expected-source-sha", "a" * 40,
        "--expected-image-id", "sha256:" + "b" * 64,
    ]) == 2


@pytest.mark.parametrize("sha,image", [
    ("invalid", "sha256:" + "a" * 64),
    ("a" * 40, "redis:7-alpine"),
    ("a" * 40, ""),
])
def test_malformed_source_or_image_fails_before_docker(monkeypatch, sha, image):
    monkeypatch.setattr(
        subject.subprocess, "run",
        lambda *a, **kw: pytest.fail("malformed preflight ran subprocess"),
    )
    assert subject.main([
        "--approve-disposable-restart",
        "--expected-source-sha", sha, "--expected-image-id", image,
    ]) == 2


def test_disposable_runner_has_no_network_no_persistent_db_or_prod_access():
    text = SCRIPT.read_text(encoding="utf-8")
    for guard in (
        '"--network", "none"', '"--read-only"',
        '"--cap-drop", "ALL"', '"--pull", "never"',
        '"--memory", "128m"', '"--cpus", "0.25"',
        '"--save", "", "--appendonly", "no"',
        '"--expected-source-sha"', '"--expected-image-id"',
        'redis_run_id_pin=pin',
        "REDIS_RESTART_FENCED_READ_DENIAL_PASS",
    ):
        assert guard in text
    for forbidden in (
        '"--publish"', '"--privileged"', "NEO4J_PASSWORD",
        "BASIC_AUTH_PASS", "PAPERCLIP_TOKEN", "/nas/",
    ):
        assert forbidden not in text


def test_cleanup_uses_immutable_container_id_not_public_name():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '["docker", "stop", "-t", "2", container_id]' in text
    assert 'cmd("docker", "restart", container_id' in text
    assert 'obj.get("Id") != expected_container_id' in text
