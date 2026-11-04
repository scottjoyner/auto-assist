"""No Docker/production access: safety contracts for disposable archive restore."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import os
import subprocess
from types import SimpleNamespace

import pytest


SOURCE=Path(__file__).resolve().parents[1]/"scripts"/"disposable_neo4j_dump_restore.py"
spec=importlib.util.spec_from_file_location("disposable_recovery",SOURCE)
assert spec and spec.loader
tool=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
PIN="sha256:"+"a"*64
DATA="Database: assistx\nFormat: Neo4j ZSTD Dump.\nFiles: 335\nBytes: 7749137182\n"


def test_valid_native_dump_metadata():
    info=tool._info(DATA)
    assert info=={"declared_file_count":335,"declared_expanded_bytes":7749137182}
    assert "path" not in str(info)


@pytest.mark.parametrize("text",[
    "", "Database: assistx\n", "Database: neo4j\nFormat: Neo4j ZSTD Dump.\nFiles: 1\nBytes: 2000000",
    "Database: assistx\nFormat: Neo4j GZIP Dump.\nFiles: 335\nBytes: 7749137182",
    "Database: assistx\nFormat: Neo4j ZSTD Dump.\nFiles: N/A\nBytes: 7749137182",
    "Database: assistx\nFormat: Neo4j ZSTD Dump.\nFiles: 335\nBytes: 2",
    "Database: assistx\nFormat: Neo4j ZSTD Dump.\nFiles: 0\nBytes: 7749137182",
    "Database: assistx\nFormat: Neo4j ZSTD Dump.\nFiles: 335\nBytes: 20000000001",
])
def test_wrong_or_empty_archive_info_never_proves_validity(text):
    with pytest.raises(tool.RecoveryHold):
        tool._info(text)


def test_build_disposable_docker_command_enforces_isolation(tmp_path):
    scratch=tmp_path/"research"
    archive=tmp_path/"live"/"backups"
    command=tool._docker_cmd(PIN,scratch,archive,"check")
    assert command[:3]==["docker","run","--rm"]
    assert command[command.index("--network")+1]=="none"
    assert "--read-only" in command
    assert "--cap-drop" in command
    assert command[command.index("--cap-drop")+1]=="ALL"
    assert command[command.index("--security-opt")+1]=="no-new-privileges"
    assert command[command.index("--tmpfs")+1]=="/tmp:rw,exec,size=768m"
    assert command[command.index("--memory")+1]=="5g"
    assert command[command.index("--cpus")+1]=="2"
    assert command[command.index("--pids-limit")+1]=="128"
    assert command[command.index("--pull")+1]=="never"
    assert command[command.index("--entrypoint")+1]=="neo4j-admin"
    assert command[-1]==PIN
    mounts=[command[i+1] for i,x in enumerate(command[:-1]) if x=="--mount"]
    assert len(mounts)==3
    assert "dst=/archive,readonly" in mounts[0]
    assert f"type=bind,src={archive}" in mounts[0]
    assert f"type=bind,src={scratch/'data'},dst=/data" in mounts
    assert f"type=bind,src={scratch/'logs'},dst=/logs" in mounts
    assert str(tmp_path/"live") not in str(mounts[1:])
    assert "--privileged" not in command
    assert "--publish" not in command
    assert "--net" not in command
    assert "--device" not in command


@pytest.mark.parametrize("suffix",["live","production","../../etc/passwd",""])
def test_rejects_arbitrary_disposable_phase(tmp_path,suffix):
    with pytest.raises(tool.RecoveryHold):
        tool._docker_cmd(PIN,tmp_path/"scratch",tmp_path/"backups",suffix)


def test_archive_staleness_requires_explicit_historical_optin(tmp_path,monkeypatch):
    live=tmp_path/"live"
    live.mkdir()
    dump=live/"assistx.dump"
    with dump.open("wb") as f:
        f.truncate(100_000_001)
    monkeypatch.setattr(tool.time,"time",lambda:1_000_000.0)
    os.utime(dump,(1_000_000-70*86400,1_000_000-70*86400))
    with pytest.raises(tool.RecoveryHold,match="historical"):
        tool._archive(live,max_age_hours=24,allow_historical=False)
    result,meta=tool._archive(live,max_age_hours=24,allow_historical=True)
    assert result==dump
    assert meta["within_requested_rpo"] is False
    assert meta["archive_age_days"]==70


def test_archive_scan_fails_closed_on_ambiguous_or_wrong_database(tmp_path):
    live=tmp_path/"live"
    live.mkdir()
    p=live/"someone_else.dump"
    with p.open("wb") as f:
        f.truncate(100_000_000)
    with pytest.raises(tool.RecoveryHold,match="filename"):
        tool._archive(live,max_age_hours=24,allow_historical=True)
    other=live/"assistx.dump"
    with other.open("wb") as f:
        f.truncate(100_000_000)
    with pytest.raises(tool.RecoveryHold,match="ambiguous"):
        tool._archive(live,max_age_hours=24,allow_historical=True)


@pytest.mark.parametrize("invalid",["invalid","sha256:"+("z"*64),"sha256:"+("a"*63),None])
def test_invalid_image_does_not_inspect_docker(monkeypatch,invalid):
    used=[]
    monkeypatch.setattr(tool,"_inspect",lambda *_:used.append(1))
    with pytest.raises(tool.RecoveryHold,match="immutable"):
        tool.verify(invalid,allow_historical=True,max_age_hours=24)
    assert used==[]


@pytest.mark.parametrize("age",[0,169,-4,True,1.5])
def test_invalid_rpo_bound_fails_before_container_inspection(monkeypatch,age):
    used=[]
    monkeypatch.setattr(tool,"_inspect",lambda *_:used.append(1))
    with pytest.raises(tool.RecoveryHold,match="rpo"):
        tool.verify(PIN,allow_historical=True,max_age_hours=age)
    assert used==[]


def test_load_step_failure_cleans_scratch_without_exposing_raw_stderr(tmp_path,monkeypatch):
    live=tmp_path/"live"
    live.mkdir()
    archive=live/"assistx.dump"
    archive.write_bytes(b"synthetic-data")
    st=archive.stat()
    monkeypatch.setattr(tool,"_inspect",lambda *_:("a"*64,live))
    monkeypatch.setattr(tool,"_archive",lambda *a,**k:(archive,{
        "archive_size_bytes":st.st_size,
        "archive_age_days":70,
        "within_requested_rpo":False,
        "original_stat":(st.st_ino,st.st_size,st.st_mtime_ns),
    }))
    monkeypatch.setattr(tool,"_sha256",lambda *_:"deadbeef")
    monkeypatch.setattr(tool.os,"statvfs",lambda *_:SimpleNamespace(f_bavail=100_000,f_frsize=1048576))
    phase_calls=[]
    def fake_phase(command,phase,timeout):
        phase_calls.append(phase)
        if phase=="info":
            return subprocess.CompletedProcess(command,0,DATA,"")
        raise tool.RecoveryHold("synthetic_load_failed")
    monkeypatch.setattr(tool,"_phase",fake_phase)
    removed=[]
    def cleanup_mock(cmd,**kwargs):
        removed.append(cmd)
        return subprocess.CompletedProcess(cmd,0,"","")
    monkeypatch.setattr(tool.subprocess,"run",cleanup_mock)
    with pytest.raises(tool.RecoveryHold,match="synthetic_load_failed"):
        tool.verify(PIN,allow_historical=True,max_age_hours=24)
    assert phase_calls==["info","load"]
    assert removed and all(cmd[:3]==["docker","rm","-f"] for cmd in removed)
    assert not list(tmp_path.glob(tool.NAME_PREFIX+"*"))
    assert archive.read_bytes()==b"synthetic-data"


def test_successful_fake_restore_and_consistency_still_hold(tmp_path,monkeypatch):
    live=tmp_path/"live"
    live.mkdir()
    archive=live/"assistx.dump"
    archive.write_bytes(b"dummy")
    st=archive.stat()
    monkeypatch.setattr(tool,"_inspect",lambda *_:("b"*64,live))
    monkeypatch.setattr(tool,"_archive",lambda *a,**k:(archive,{
        "archive_size_bytes":st.st_size,
        "archive_age_days":70,
        "within_requested_rpo":False,
        "original_stat":(st.st_ino,st.st_size,st.st_mtime_ns),
    }))
    monkeypatch.setattr(tool,"_sha256",lambda *_:"a"*64)
    monkeypatch.setattr(tool.os,"statvfs",lambda *_:SimpleNamespace(f_bavail=100_000,f_frsize=1048576))
    phases=[]
    def fake_phase(cmd,phase,timeout):
        phases.append(phase)
        if phase=="load":
            mount=next(cmd[i+1] for i,x in enumerate(cmd[:-1]) if x=="--mount"
                       and "dst=/data" in cmd[i+1])
            dest=Path(mount.split("src=",1)[1].split(",dst=",1)[0])
            restored=dest/"databases"/"assistx"
            restored.mkdir(parents=True)
            (restored/"synthetic-neostore").write_bytes(b"not private")
        return subprocess.CompletedProcess(cmd,0,DATA if phase=="info" else "ok","")
    monkeypatch.setattr(tool,"_phase",fake_phase)
    monkeypatch.setattr(tool.subprocess,"run",lambda cmd,**k:subprocess.CompletedProcess(cmd,0,"",""))
    result=tool.verify(PIN,allow_historical=True,max_age_hours=24)
    assert phases==["info","load","consistency"]
    assert result["research_disposable_archive_restore"]=="PASS"
    assert result["offline_full_consistency"]=="PASS"
    assert result["within_requested_rpo"] is False
    assert result["production_decision"]=="HOLD"
    assert result["cutover_authorized"] is False
    assert result["current_recovery_point_verified"] is False
    assert result["independent_custody_verified"] is False
    assert not list(tmp_path.glob(tool.NAME_PREFIX+"*"))


def test_cli_requires_explicit_disposable_restore_without_inspecting(monkeypatch):
    calls=[]
    monkeypatch.setattr(tool,"verify",lambda *a,**kw:calls.append(1))
    with pytest.raises(SystemExit) as e:
        tool.main(["--expected-image",PIN,"--json"])
    assert e.value.code==2
    assert calls==[]


def test_cli_redacts_internal_error_and_always_exits_hold(monkeypatch,capsys):
    def bad(*a,**kw):
        raise RuntimeError("password=SECRET host.example unsafe")
    monkeypatch.setattr(tool,"verify",bad)
    code=tool.main(["--expected-image",PIN,"--json","--explicit-disposable-restore"])
    out=capsys.readouterr().out
    assert code==3
    assert "SECRET" not in out and "host.example" not in out
    assert json.loads(out)["production_decision"]=="HOLD"


def test_no_application_mutator_route_or_background_job():
    root=SOURCE.parents[1]
    for name in ("src/assistx/api.py","src/assistx/jobs.py","src/assistx/routers/review.py"):
        assert "disposable_neo4j_dump_restore" not in (root/name).read_text()
    content=SOURCE.read_text()
    assert '["docker", "inspect", "neo4j"]' in content
    assert "--network\",\"none" in content
    assert "--read-only" in content
    assert "--tmpfs\",\"/tmp:rw,exec,size=768m" in content
    for disallowed in ("systemctl restart","iptables -A","nft add","docker volume rm",
                       "GRANT WRITE","CREATE (t:Task)","neo4j-admin database dump"):
        assert disallowed not in content
