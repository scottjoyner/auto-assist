"""Synthetic, socketless custody tests for read-only fleet source inspection."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fleet_node_source_census.py"
spec = importlib.util.spec_from_file_location("fleet_source_census", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def source_tree(tmp_path, *, loader_root=None):
    root = tmp_path / "repo" / "src" / "assistx"
    root.mkdir(parents=True)
    for name in audit.MODULES:
        data = 'LEGACY = "state=READY"\n' if name == "fleet_node_agent" else "VALUE = 1\n"
        (root / f"{name}.py").write_text(data)
    launcher = tmp_path / "fleet-work" / "run_fleet_node_agent.py"
    launcher.parent.mkdir()
    chosen = loader_root or root
    launcher.write_text(
        "import importlib.util\n"
        f"ROOT = {str(chosen)!r}\n"
        "for name in ('kv_cache', 'recovery_runbooks', 'fleet_node_agent'):\n"
        "    spec = importlib.util.spec_from_file_location(f'assistx.{name}', f'{ROOT}/{name}.py')\n"
        "agent = None\n"
        "agent.DEFAULT_CAPS = []\n"
    )
    return launcher, root


def test_source_hashes_are_nonadmitting(tmp_path):
    launcher, root = source_tree(tmp_path)
    observed = audit.inspect_loader(launcher, process_start_epoch=time.time() + 30)
    content = (root / "fleet_node_agent.py").read_bytes()
    assert observed["sources"]["fleet_node_agent"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert observed["sources"]["fleet_node_agent"]["legacy_state_ready_marker"] is True
    assert observed["sources"]["fleet_node_agent"]["node_token_marker"] is False
    assert not observed["sources"]["fleet_node_agent"]["changed_after_start"]


def test_modified_source_since_start_is_flagged(tmp_path):
    launcher, root = source_tree(tmp_path)
    observed = audit.inspect_loader(launcher, process_start_epoch=time.time() - 3600)
    assert observed["sources"]["fleet_node_agent"]["changed_after_start"] is True
    assert observed["loader_changed_after_start"] is True


def test_unknown_loader_root_rejected(tmp_path):
    launcher, _ = source_tree(tmp_path)
    launcher.write_text("import importlib.util\nROOT = '/tmp/unrelated'\n")
    with pytest.raises(audit.CensusHold, match="untrusted_loader_root"):
        audit.inspect_loader(launcher, process_start_epoch=0)


def test_missing_root_assignment_rejected(tmp_path):
    launcher, _ = source_tree(tmp_path)
    launcher.write_text("import importlib.util\n")
    with pytest.raises(audit.CensusHold, match="unrecognized_loader_root"):
        audit.inspect_loader(launcher, process_start_epoch=0)


def test_symlinked_source_file_not_followed(tmp_path):
    launcher, root = source_tree(tmp_path)
    source = root / "fleet_node_agent.py"
    source.unlink()
    sentinel = tmp_path / "private-sentinel"
    sentinel.write_text("synthetic-secret-do-not-print")
    source.symlink_to(sentinel)
    with pytest.raises(audit.CensusHold, match="missing_or_linked_file"):
        audit.inspect_loader(launcher, process_start_epoch=0)
    assert sentinel.read_text() == "synthetic-secret-do-not-print"


def test_source_contents_never_appear_in_evidence(tmp_path):
    launcher, root = source_tree(tmp_path)
    secret_sentinel = "synthetic-token-must-not-leak"
    (root / "fleet_node_agent.py").write_text(f"PASSWORD = {secret_sentinel!r}\n")
    observed = audit.inspect_loader(launcher, process_start_epoch=time.time())
    assert secret_sentinel not in json.dumps(observed)


def test_running_process_has_no_executable_memory_attestation(tmp_path):
    launcher, _ = source_tree(tmp_path)
    proc = tmp_path / "proc"
    (proc / "777").mkdir(parents=True)
    (proc / "777" / "cwd").symlink_to(launcher.parent, target_is_directory=True)
    (proc / "777" / "exe").symlink_to("/usr/bin/python3.12 (deleted)")
    (proc / "777" / "cmdline").write_bytes(b"python3\0" + str(launcher).encode() + b"\0")
    uptime = 120.0
    (proc / "uptime").write_text(f"{uptime} 200.0\n")
    fields = ["0"] * 35
    fields[19] = str(int(100 * os.sysconf("SC_CLK_TCK")))
    (proc / "777" / "stat").write_text("777 (python3) S " + " ".join(fields) + "\n")
    result = audit.inspect_process(777, proc=proc)
    assert result["running_process_present"]
    assert result["runtime_executable_replaced"]
    assert result["loaded_module_identity_proven"] is False
    assert result["task_admission"] == "HOLD"
    assert result["decision"] == "HOLD_SOURCE_AND_RUNTIME_NOT_ATTESTED"


def test_unexpected_argv_is_rejected_without_echo(tmp_path):
    launcher, _ = source_tree(tmp_path)
    proc = tmp_path / "proc"
    (proc / "777").mkdir(parents=True)
    secret = b"--auth-password=synthetic-secret-do-not-print"
    (proc / "777" / "cmdline").write_bytes(b"python3\0" + str(launcher).encode() + b"\0" + secret + b"\0")
    (proc / "777" / "cwd").symlink_to(launcher.parent, target_is_directory=True)
    (proc / "777" / "exe").symlink_to("/usr/bin/python3")
    with pytest.raises(audit.CensusHold, match="unrecognized_running_command") as exc:
        audit.inspect_process(777, proc=proc)
    assert "synthetic-secret" not in str(exc.value)


def test_nonempty_default_capabilities_rejected(tmp_path):
    launcher, _ = source_tree(tmp_path)
    original = launcher.read_text()
    launcher.write_text(original.replace("agent.DEFAULT_CAPS = []", "agent.DEFAULT_CAPS = ['llm']"))
    with pytest.raises(audit.CensusHold, match="unsupported_launcher_contract"):
        audit.inspect_loader(launcher, process_start_epoch=0)


def test_missing_dynamic_import_contract_rejected(tmp_path):
    launcher, _ = source_tree(tmp_path)
    original = launcher.read_text()
    launcher.write_text(original.replace("spec_from_file_location", "some_other_loader"))
    with pytest.raises(audit.CensusHold, match="unsupported_launcher_contract"):
        audit.inspect_loader(launcher, process_start_epoch=0)
