"""Safety and offline contract for explicit x1-370 to Lenovo witness canary."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest

import hermes_high_water_offhost_canary as canary


def test_default_is_hold_without_ssh(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("dry run must not start commands")))
    # Invoke main through runpy with no physical opt-in.
    import runpy
    monkeypatch.setattr(sys,"argv",[canary.__file__])
    from io import StringIO
    cap=StringIO()
    monkeypatch.setattr(sys,"stdout",cap)
    with pytest.raises(SystemExit) if False else __import__("contextlib").nullcontext():
        runpy.run_path(canary.__file__,run_name="__main__")
    doc=json.loads(cap.getvalue())
    assert doc["decision"]=="HOLD"
    assert doc["physical_executed"] is False


def test_remote_code_is_single_use_and_in_memory_only():
    code=canary.REMOTE_SIGNER
    assert "Ed25519PrivateKey.generate()" in code
    assert "sys.stdin.buffer.readline(4096)" in code
    assert '"high_water_generation":2' in code
    assert "serialization.Encoding.Raw" in code
    for forbidden in ("open(", "Path(", "shutil", "sudo", "mount ",
                      "rm -", "rsync", "pickle", "eval("):
        assert forbidden not in code


def test_same_administrator_does_not_count_as_independence():
    s=Path(canary.__file__).read_text()
    assert '"authority_independently_administered":False' in s
    assert '"durable_high_water_verified":False' in s
    assert 'result=[run_one(generation=2' in s
    assert 'run_one(generation=1' in s


def test_ssh_requires_trusted_existing_host_key_and_batch_mode():
    s=Path(canary.__file__).read_text()
    assert '"StrictHostKeyChecking=yes"' in s
    assert '"BatchMode=yes"' in s
    assert '"ConnectTimeout=4"' in s
    assert 'process.stdin.write(req)' in s
    assert 'process.stdout' in s


def test_no_production_mount_credentials_models_or_nas_paths_in_canary():
    s=Path(canary.__file__).read_text()
    for forbidden in ("/media/scott/SSD_4TB", "/media/scott/NAS5",
                      "/root/.ssh", "docker run", "docker exec",
                      "sshpass", "curl ", "wget "):
        assert forbidden not in s
    assert 'TemporaryDirectory' in s
    assert 'dir="/home/scott/.cache"' in s
    assert "process.terminate()" in s
    assert 'process.wait(timeout=6)' in s


def test_prod_identity_is_pinned_and_untouched():
    assert canary.EXPECTED_LIVE.startswith("sha256:")
    assert len(canary.EXPECTED_LIVE)==71
    s=Path(canary.__file__).read_text()
    assert '"assistx-hermes-adapter"' in s
    assert 'after.stdout' in s
    assert '"production_changed":False' in s


def test_line_with_timeout_rejects_nonterminating_remote(monkeypatch):
    monkeypatch.setattr(canary.select,"select",lambda *_a, **_k:([],[],[]))
    with pytest.raises(RuntimeError,match="timeout"):
        canary.line_with_timeout(object(),0.01)


@pytest.mark.parametrize("content", [b"", b"x"*4098, b"partial"])
def test_line_with_timeout_rejects_bad_framing(monkeypatch,content):
    stream=Mock()
    stream.readline.return_value=content
    monkeypatch.setattr(canary.select,"select",lambda *_a, **_k:([stream],[],[]))
    with pytest.raises(RuntimeError):
        canary.line_with_timeout(stream)


def test_line_with_timeout_accepts_bounded_complete_receipt(monkeypatch):
    stream=Mock()
    stream.readline.return_value=b"test-receipt\n"
    monkeypatch.setattr(canary.select,"select",lambda *_a, **_k:([stream],[],[]))
    assert canary.line_with_timeout(stream)==b"test-receipt\n"


def test_source_requires_explicit_optin_and_never_makes_live_writes():
    s=Path(canary.__file__).read_text()
    assert '"--physical-disposable-lenovo"' in s
    assert 'os.uname().nodename=="x1-370"' in s
    assert 'os.geteuid()==1000' in s
    assert 'provider_requests":0' in s
