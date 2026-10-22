"""Offline safety controls for disposable remote SQLite witness research."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest

import hermes_durable_witness_offhost_canary as c


def test_default_inert_without_optin(monkeypatch,capsys):
    import runpy
    def deny(*_args,**_kwargs):raise AssertionError("dry-run must not access SSH")
    monkeypatch.setattr(subprocess,"run",deny)
    monkeypatch.setattr(sys,"argv",[c.__file__])
    runpy.run_path(c.__file__,run_name="__main__")
    out=json.loads(capsys.readouterr().out)
    assert out == {"decision":"HOLD","physical_executed":False}


def test_remote_script_uses_durable_transactional_ledger_without_source_install():
    code=c.REMOTE_CODE
    assert 'exec(compile(base64.b64decode(raw),name,"exec")' in code
    assert "ledger.ResearchHighWaterLedger" in code
    assert '"ledger.sqlite"' in code
    assert '"signer.key"' in code
    assert 'os.fsync(stream.fileno())' in code
    assert "Ed25519PrivateKey.generate()" in code
    assert "with ledger.ResearchHighWaterLedger" in code


def test_key_and_ledger_are_disposable_and_remote_only():
    code=c.REMOTE_CODE
    assert 'os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600' in code
    assert '"og-hermes-ledger-"' in code
    assert '"cleanup"' in code
    assert "shutil.rmtree(path)" in code
    assert 'private_bytes' in code
    assert 'print(private)' not in code


def test_remote_path_and_ownership_are_strictly_validated():
    code=c.REMOTE_CODE
    assert '/tmp/og-hermes-ledger-' in code
    assert 'meta.st_uid!=os.geteuid()' in code
    assert 'meta.st_mode&0o077' in code
    assert 'not stat.S_ISREG(meta.st_mode)' in code
    assert 'directory=sys.argv[4]' in code
    assert "os.geteuid()==1000" in Path(c.__file__).read_text()


def test_no_production_change_or_real_data_access_in_source():
    source=Path(c.__file__).read_text()
    assert 'docker","inspect"' in source
    assert 'production_modified":False' in source
    for forbidden in (
        "/media/scott/SSD_4TB","/media/scott/NAS5",
        "docker run","docker exec","rsync","/root/.ssh",
        "sshpass","scp ","sudo ","curl ","wget "
    ):
        assert forbidden not in source


def test_explicit_existing_ssh_hostkey_validation():
    source=Path(c.__file__).read_text()
    assert '"StrictHostKeyChecking=yes"' in source
    assert '"BatchMode=yes"' in source
    assert '"ConnectTimeout=4"' in source
    assert '"ConnectionAttempts=1"' in source


def test_generation_2_accepted_then_generation_1_denied():
    a=c.challenge(2)
    b=c.challenge(1)
    assert a["generation"]==2
    assert a["checkpoint_sha256"]=="2"*64
    assert b["manifest_sha256"]=="a"*64
    assert a["nonce"]!=b["nonce"]
    assert len(a["nonce"])==64


def test_no_auto_enroll_from_worker_queries():
    code=c.REMOTE_CODE
    assert code.count("db.publish(")==2
    assert 'action=="provision"' in code
    assert 'action=="challenge"' in code
    assert 'db.respond(raw_request=request,signer=signer)' in code


def test_remote_signer_revocation_and_restart_proof():
    code=c.REMOTE_CODE
    assert 'elif action=="revoke"' in code
    assert 'db.revoke(owner)' in code
    source=Path(c.__file__).read_text()
    assert '"revocation_survived_process_restart":True' in source
    assert 'response["pid"]!=created["pid"]' in source


def test_cleanup_in_finally_even_after_witness_error():
    source=Path(c.__file__).read_text()
    assert "finally:" in source
    assert 'removed=remote(payload,"cleanup",path)' in source
    assert 'need(removed["removed"]' in source
    assert 'need(shell(production)==before' in source


def test_source_explicitly_denies_independent_and_whole_db_claims():
    source=Path(c.__file__).read_text()
    assert '"witness_independently_administered":False' in source
    assert '"whole_database_rollback_resistant":False' in source
    assert '"decision":"HOLD"' in source


def test_mocked_remote_error_fails_closed(monkeypatch):
    monkeypatch.setattr(c.subprocess,"run",lambda *_a,**_kw:
                        Mock(returncode=255,stdout="",stderr=""))
    with pytest.raises(c.UnsafeCanary):
        c.shell(["ssh","invalid"],timeout=0.5)


def test_exact_pinned_ledger_blob_and_image():
    assert c.EXPECTED_HIGH=="ed8992eef765006cb6a12def4553b04d0129ecbe"
    assert len(c.EXPECTED_LEDGER)==40
    assert len(c.EXPECTED_IMAGE)==71
    assert c.LEDGER.is_file() and c.HIGH.is_file()


def test_preflight_explicitly_not_independent():
    assert "Shared SSH control" in c.__doc__
    assert "NEVER production custody" in c.__doc__
