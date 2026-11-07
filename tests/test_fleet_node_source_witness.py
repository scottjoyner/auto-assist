"""No SSH, credentials, production process or Task execution in these tests."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/fleet_node_source_witness.py"
spec = importlib.util.spec_from_file_location("fleet_node_source_witness", SOURCE)
assert spec and spec.loader
witness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(witness)


def _case(tmp_path, monkeypatch):
    monkeypatch.setattr(witness.Path, "home", lambda: tmp_path)
    wrapper = tmp_path / "fleet-work/run_fleet_node_agent.py"
    wrapper.parent.mkdir()
    root = tmp_path / "source"
    root.mkdir()
    agent = root / "fleet_node_agent.py"
    agent.write_text("# synthetic static agent source\n", encoding="utf-8")
    wrapper.write_text(
        f"ROOT = {str(root)!r}\n"
        "for name in ('kv_cache','recovery_runbooks','fleet_node_agent'):\n"
        "    pass\n", encoding="utf-8"
    )
    unit = "[Service]\nExecStart=/usr/bin/python3 %h/fleet-work/run_fleet_node_agent.py\n"
    expected = hashlib.sha256(agent.read_bytes()).hexdigest()
    args = dict(unit_text=unit, wrapper=wrapper, source_root=str(root),
                expected_sha=expected, pid_started=agent.stat().st_mtime + 20, active=True)
    return args, agent


def test_matching_disk_never_authorizes_loaded_code(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    result = witness.evaluate(**args)
    assert result["source_on_disk_matches_candidate"] is True
    assert result["status"] == "HOLD"
    assert result["task_admission"] == "HOLD"
    assert result["running_code_attested"] is False
    assert result["reason_codes"] == ["running_python_code_cannot_be_attested_from_disk"]


def test_mismatch_hash_is_explicit_hold(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["expected_sha"] = "0" * 64
    result = witness.evaluate(**args)
    assert result["source_on_disk_matches_candidate"] is False
    assert "source_hash_mismatch" in result["reason_codes"]


def test_changed_source_after_start_is_hold(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["pid_started"] = 1
    result = witness.evaluate(**args)
    assert "source_changed_after_unit_start" in result["reason_codes"]
    assert "wrapper_changed_after_unit_start" in result["reason_codes"]


def test_unit_script_mismatch_is_hold(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["unit_text"] = "ExecStart=/usr/bin/python3 %h/fleet-work/different.py\n"
    assert "unit_wrapper_not_pinned" in witness.evaluate(**args)["reason_codes"]


def test_wrapper_root_mismatch_is_hold(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["source_root"] += "-wrong"
    assert "source_root_mismatch" in witness.evaluate(**args)["reason_codes"]


def test_symlink_source_is_denied(tmp_path, monkeypatch):
    args, agent = _case(tmp_path, monkeypatch)
    destination = agent.parent / "orig.py"
    agent.rename(destination)
    agent.symlink_to(destination)
    assert "source_path_symlink" in witness.evaluate(**args)["reason_codes"]


def test_missing_static_wrapper_root_denied(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["wrapper"].write_text("ROOT = make_root()\nfor name in ('fleet_node_agent',): pass\n")
    assert "wrapper_source_root_not_static" in witness.evaluate(**args)["reason_codes"]


def test_unit_multiple_execstart_denied(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["unit_text"] += "ExecStart=/usr/bin/python3 /elsewhere.py\n"
    assert "unit_execstart_not_unique" in witness.evaluate(**args)["reason_codes"]


def test_inactive_service_stays_hold(tmp_path, monkeypatch):
    args, _ = _case(tmp_path, monkeypatch)
    args["active"] = False
    assert "unit_not_running" in witness.evaluate(**args)["reason_codes"]


def test_main_always_exits_two_and_never_calls_task_api(tmp_path, monkeypatch, capsys):
    args, _ = _case(tmp_path, monkeypatch)
    unit_path = tmp_path / "fleet-node-agent.service"
    unit_path.write_text(args["unit_text"])
    show = ("ActiveState=active\nMainPID=123456\nFragmentPath=" + str(unit_path) + "\n")
    called = []
    def fake_run(argv, **kwargs):
        called.append(argv)
        return SimpleNamespace(stdout=show, returncode=0)
    monkeypatch.setattr(witness.subprocess, "run", fake_run)
    monkeypatch.setattr(witness, "_pid_start_epoch", lambda _pid: args["pid_started"])
    assert witness.main(["--expected-source-root", args["source_root"],
                         "--expected-source-sha256", args["expected_sha"]]) == 2
    import json
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "HOLD" and result["task_admission"] == "HOLD"
    assert len(called) == 1 and called[0][:3] == ["systemctl", "--user", "show"]
    assert not any("api" in str(arg).lower() or "task" in str(arg).lower() for arg in called[0])
