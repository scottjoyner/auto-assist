"""Path separation and safe controller command construction (no live SSH)."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/trace_execution_shadow_control.py"
CONFIG = Path(__file__).resolve().parents[1] / "config/trace_execution_shadow_nodes.json"


def module():
    spec = importlib.util.spec_from_file_location("shadow_paths_test", SCRIPT)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def test_exactly_two_separate_disabled_paths():
    paths = module().load_paths(str(CONFIG))
    assert set(paths) == {"xwing", "scotts-macbook-air"}
    assert paths["xwing"]["audit_root"] != paths["scotts-macbook-air"]["audit_root"]
    assert paths["xwing"]["release_root"] != paths["scotts-macbook-air"]["release_root"]
    assert paths["xwing"]["platform"] == "linux"
    assert paths["scotts-macbook-air"]["platform"] == "macos"
    assert all(n["production_worker_enabled"] is False for n in paths.values())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda d: d.update(live_dispatch_enabled=True),
        lambda d: d.update(mode="live"),
        lambda d: d["nodes"][1].update(node_id="xwing"),
        lambda d: d["nodes"][1].update(audit_root=d["nodes"][0]["audit_root"]),
        lambda d: d["nodes"][0].update(release_root="/home/scott/../escape/assistx-trace-shadow/v1"),
        lambda d: d["nodes"][0].update(ssh_target="xwing;echo-pwn"),
        lambda d: d["nodes"][0].update(production_worker_enabled=True),
    ],
)
def test_registry_rejects_unsafe_changes(tmp_path, mutator):
    data = json.loads(CONFIG.read_text())
    mutator(data)
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        module().load_paths(str(path))


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("verify", "--verify-only"),
        ("noop", "trace_execution_canary.py"),
        ("negative", "trace_execution_shadow_negative.py"),
    ],
)
def test_controller_only_builds_fixed_ssh_synthetic_commands(monkeypatch, mode, expected):
    obj = module()
    node = obj.load_paths(str(CONFIG))["scotts-macbook-air"]
    capture = {}

    def fake_run(argv, **kwargs):
        capture["argv"] = argv
        assert kwargs["check"] is False
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"synthetic": True, "ok": True, "verification": {"ok": True}}), stderr=""
        )

    monkeypatch.setattr(obj.subprocess, "run", fake_run)
    result = obj.run(node, canary=(mode == "noop"), negative=(mode == "negative"))
    assert result["ok"] is True
    argv = capture["argv"]
    assert argv[0] == "ssh"
    assert argv[-2] == node["ssh_target"]
    assert "StrictHostKeyChecking=yes" in argv
    assert "BatchMode=yes" in argv
    assert expected in argv[-1]
    assert "PYTHONPATH=" in argv[-1]
