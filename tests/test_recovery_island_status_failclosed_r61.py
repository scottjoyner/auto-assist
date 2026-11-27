"""Round 61: recovery-island status must not report green on failed health checks.

``RecoveryIslandExecutor.status()`` runs the real verification probes when a
deployment is active, but returns ``"ok": True`` unconditionally. An active
island whose health endpoints all answer 503 is therefore published as healthy
by the status CLI/API. This is the same fail-open evidence class as the other
sweep findings, in a distinct verdict (status(), not runbook verification).

Hermetic: in-process stubs only. No network, subprocess, service, container,
credential or live fleet state is touched; the executor's state directory is
pytest's tmp_path under the private SSD scratch dir.
"""

from __future__ import annotations

import hashlib
import json

from assistx.recovery_island import RecoveryIslandExecutor

NODE = "node-r61"
KEY_ID = "r61-key"
SECRET = "r61-secret"
HEALTH_URL = "http://127.0.0.1:28000/health"


def _layout(tmp_path):
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "compose.recovery.yml").write_text(
        "services:\n  api:\n    image: assistx:test\n", encoding="utf-8"
    )
    bundle = tmp_path / "bundle.tar"
    bundle.write_bytes(b"offline-image-bundle")
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"bundle_sha256": bundle_sha, "images": []}),
        encoding="utf-8",
    )
    config = {
        "assistx": {
            "project_directory": str(root),
            "project_name": "assistx_recovery_island",
            "compose_files": ["compose.recovery.yml"],
            "services": ["api"],
            "bundle_path": str(bundle),
            "manifest_path": str(manifest),
            "bundle_sha256": bundle_sha,
            "health_urls": [HEALTH_URL],
        }
    }
    return config


def _executor(tmp_path, http_status=200, probes=None):
    def http(_method, url, **_kwargs):
        if probes is not None:
            probes.append(url)
        return http_status, {"status": "ok" if http_status == 200 else "down"}

    return RecoveryIslandExecutor(
        node_id=NODE,
        state_dir=str(tmp_path / "state"),
        http=http,
        runner=lambda *_args, **_kwargs: None,
        env={
            "FLEET_RECOVERY_ISLAND_DEPLOYMENTS": json.dumps(_layout(tmp_path))
        },
        runbook_keys={KEY_ID: SECRET},
        activation_keys={KEY_ID: SECRET},
    )


def _activate(tmp_path):
    (tmp_path / "state" / "assistx.active.json").write_text(
        json.dumps({"status": "active"}), encoding="utf-8"
    )


def test_active_island_with_failed_health_probe_is_not_ok(tmp_path):
    """A 503 from every health URL is a failed measurement, not a green island."""
    executor = _executor(tmp_path, http_status=503)
    _activate(tmp_path)

    result = executor.status("assistx")

    assert result["verification"]["ok"] is False
    assert result["ok"] is False


def test_active_island_with_truthy_non_bool_verdict_is_not_ok(tmp_path, monkeypatch):
    """A non-boolean 'ok' string carries no affirmative liveness evidence."""
    executor = _executor(tmp_path)
    _activate(tmp_path)
    monkeypatch.setattr(
        executor,
        "_verify",
        lambda *_args, **_kwargs: {
            "ok": "true",
            "status": "verified",
            "checks": [],
        },
    )

    result = executor.status("assistx")

    assert result["ok"] is False


def test_active_island_with_healthy_probe_is_ok(tmp_path):
    """Control (passes on base): real healthy evidence still reports ok."""
    probes = []
    executor = _executor(tmp_path, http_status=200, probes=probes)
    _activate(tmp_path)

    result = executor.status("assistx")

    assert probes == [HEALTH_URL]
    assert result["verification"]["ok"] is True
    assert result["ok"] is True
    assert result["status"] == "active"


def test_inactive_island_does_not_probe_and_remains_ok(tmp_path):
    """Control (passes on base): no active deployment means no probe to fail."""
    probes = []
    executor = _executor(tmp_path, http_status=503, probes=probes)

    result = executor.status("assistx")

    assert probes == []
    assert result["verification"] is None
    assert result["ok"] is True
    assert result["status"] == "empty"
