"""Synthetic, network-free negative tests for native backup readiness census."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time

import pytest


SRC=Path(__file__).resolve().parents[1]/"scripts"/"read_only_neo4j_backup_readiness.py"
spec=importlib.util.spec_from_file_location("backup_readiness_research",SRC)
assert spec and spec.loader
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
IMAGE="sha256:"+"a"*64
API_IMAGE="sha256:"+"b"*64
CID="c"*64
START="2026-11-03T19:55:00Z"
TCP_HEADER="  sl  local_address rem_address st tx_queue\n"


def proc_line(address: str, family: str="tcp6", port: int=6362) -> str:
    ip=ipaddress.ip_address(address)
    raw=ip.packed
    if family=="tcp":
        assert len(raw)==4
        value=raw[::-1].hex().upper()
    else:
        if isinstance(ip,ipaddress.IPv4Address):
            ip=ipaddress.IPv6Address("::ffff:"+address)
            raw=ip.packed
        value=b"".join(raw[i:i+4][::-1] for i in (0,4,8,12)).hex().upper()
    return f"   0: {value}:{port:04X} {value}:0000 0A 0 0 0 100 0 0\n"


def image_obj(tmp_path, name="neo4j", image=IMAGE, running=True, cid=CID):
    return {
        "Name":"/"+name, "Image":image, "Id":cid,
        "State":{"Running":running,"StartedAt":START},
        "HostConfig":{"PortBindings":{"7687/tcp":[{"HostIp":"0.0.0.0","HostPort":"7688"}]}},
        "Mounts":[{"Destination":"/data","Type":"bind","Source":str(tmp_path),"RW":True}],
    }


def basic_settings(enabled=True,scope="loopback",port=True):
    return {"expected_settings_present":True,"enabled":enabled,
            "listen_scope":scope,"listen_port_6362":port}


def assess(tmp_path, *, settings=None, tcp=None, tcp6=None, graph=None):
    db=graph or image_obj(tmp_path)
    api=image_obj(tmp_path,name="assistx-api",image=API_IMAGE)
    return gate.assess(db,api,settings or basic_settings(),
        tcp if tcp is not None else TCP_HEADER,
        tcp6 if tcp6 is not None else TCP_HEADER+proc_line("127.0.0.1"),
        now=time.time(),max_age_hours=24)


def test_loopback_enabled_no_host_published_still_hold(tmp_path):
    x=assess(tmp_path)
    assert x["production_decision"]=="HOLD"
    assert x["native_backup_enabled"] is True
    assert x["native_backup_listen_setting_loopback"] is True
    assert x["actual_backup_kernel_listeners"]==1
    assert x["actual_backup_listeners_all_loopback"] is True
    assert x["host_published_backup_bindings"]==0
    assert x["backup_run_authorized"] is False
    assert x["production_cutover_authorized"] is False
    assert x["dispatch_authorized"] is False
    assert x["fresh_backup_restorability_verified"] is False
    assert x["network_reachability_verified"] is False
    assert x["no_production_writes"] is True


@pytest.mark.parametrize("address,family,want",[
    ("127.0.0.1","tcp","loopback"),
    ("127.0.0.1","tcp6","loopback"),
    ("::1","tcp6","loopback"),
    ("0.0.0.0","tcp","wildcard"),
    ("::","tcp6","wildcard"),
    ("192.168.1.5","tcp","nonloopback"),
    ("2001:db8::1","tcp6","nonloopback"),
])
def test_decodes_raw_kernel_listen_scope(address,family,want):
    assert gate._kernel_listeners(
        TCP_HEADER+proc_line(address,family) if family=="tcp" else TCP_HEADER,
        TCP_HEADER+proc_line(address,family) if family=="tcp6" else TCP_HEADER,
    )==[want]


@pytest.mark.parametrize("address",["::","0.0.0.0","192.168.1.2","2001:db8::2"])
def test_non_loopback_listener_denies_physical_policy(tmp_path,address):
    x=assess(tmp_path,tcp6=TCP_HEADER+proc_line(address))
    assert x["actual_backup_listeners_all_loopback"] is False
    assert "kernel_backup_listener_does_not_match_loopback_policy" in x["risk_flags"]


def test_missing_socket_never_proves_ready(tmp_path):
    x=assess(tmp_path,tcp6=TCP_HEADER)
    assert x["actual_backup_kernel_listeners"]==0
    assert x["actual_backup_listeners_all_loopback"] is False
    assert "kernel_backup_listener_does_not_match_loopback_policy" in x["risk_flags"]


def test_production_backup_port_host_publication_is_warning(tmp_path):
    graph=image_obj(tmp_path)
    graph["HostConfig"]["PortBindings"]["6362/tcp"]=[
        {"HostIp":"0.0.0.0","HostPort":"6362"}]
    x=assess(tmp_path,graph=graph)
    assert x["host_published_backup_bindings"]==1
    assert "backup_service_host_published" in x["risk_flags"]


def test_untrusted_malformed_port_bindings_raise(tmp_path):
    graph=image_obj(tmp_path)
    graph["HostConfig"]["PortBindings"]["6362/tcp"]="bad"
    with pytest.raises(gate.BackupHold,match="port_bindings"):
        assess(tmp_path,graph=graph)


def test_disabled_native_setting_is_not_ignored(tmp_path):
    x=assess(tmp_path,settings=basic_settings(enabled=False))
    assert x["native_backup_enabled"] is False
    assert "backup_server_disabled" in x["risk_flags"]


def test_native_setting_does_not_match_kernel(tmp_path):
    x=assess(tmp_path,settings=basic_settings(scope="wildcard"))
    assert "backup_listener_setting_not_loopback" in x["risk_flags"]
    x=assess(tmp_path,settings=basic_settings(port=False))
    assert "kernel_backup_listener_does_not_match_loopback_policy" in x["risk_flags"]


def test_no_confirmed_native_settings_fail_closed(tmp_path):
    with pytest.raises(gate.BackupHold,match="settings_unverified"):
        assess(tmp_path,settings={"expected_settings_present":False})


def test_archive_inventory_only_metadata_fresh_and_stale(tmp_path):
    now=time.time()
    recent=tmp_path/"fresh.backup"
    recent.write_bytes(b"synthetic")
    old=tmp_path/"assistx.dump"
    old.write_bytes(b"not-a-real-dump")
    os.utime(old,(now-70*86400,now-70*86400))
    x=gate._archive_metadata(tmp_path,now,24)
    assert x["fresh_archive_count_in_data_mount"]==1
    assert x["backup_chain_file_count"]==1
    assert x["historical_dump_count"]==1
    assert 69<x["oldest_archive_age_days"]<71
    assert x["outside_graph_data_mount_backups_verified"] is False
    assert x["archive_bytes_not_read"] is True
    assert "synthetic" not in json.dumps(x)


def test_old_dump_only_does_not_satisfy_rpo(tmp_path):
    p=tmp_path/"assistx.dump"
    p.write_bytes(b"dummy")
    now=time.time()
    os.utime(p,(now-70*86400,now-70*86400))
    x=assess(tmp_path)
    assert x["archive_inventory"]["historical_dump_count"]==1
    assert x["archive_inventory"]["fresh_archive_count_in_data_mount"]==0
    assert "no_fresh_archive_in_scoped_graph_data_inventory" in x["risk_flags"]


def test_symlink_archive_never_trusted(tmp_path):
    (tmp_path/"assistx.dump").symlink_to(tmp_path/"other")
    with pytest.raises(gate.BackupHold,match="symlink"):
        gate._archive_metadata(tmp_path,time.time(),24)


@pytest.mark.parametrize("bad",["garbage","sha256:"+"z"*64,"sha256:"+"a"*63,None])
def test_image_digest_refused_before_running_docker(monkeypatch,bad):
    calls=[]
    monkeypatch.setattr(gate,"_call",lambda *a,**kw:calls.append(1))
    with pytest.raises(gate.BackupHold,match="immutable"):
        gate._inspect("neo4j",bad)
    assert calls==[]


def test_docker_inspect_wrong_image_or_stopped_or_name(tmp_path,monkeypatch):
    for variant in ("wrong_image","stopped","wrong_name"):
        obj=image_obj(tmp_path)
        if variant=="wrong_image":
            obj["Image"]=API_IMAGE
        if variant=="stopped":
            obj["State"]["Running"]=False
        if variant=="wrong_name":
            obj["Name"]="/not-neo4j"
        monkeypatch.setattr(gate,"_call",lambda *a,**kw:json.dumps([obj]))
        with pytest.raises(gate.BackupHold,match="runtime_image_or_state"):
            gate._inspect("neo4j",IMAGE)


def test_collect_detects_restart_between_inspections(monkeypatch,tmp_path):
    graph=image_obj(tmp_path)
    api=image_obj(tmp_path,name="assistx-api",image=API_IMAGE)
    graph_after=deepcopy(graph)
    graph_after["State"]["StartedAt"]="2026-11-03T20:30:00Z"
    vals=iter([graph,api,graph_after,api])
    monkeypatch.setattr(gate,"_inspect",lambda name,image:next(vals))
    def fake_call(command,**kw):
        if command[:3]==["docker","exec","-i"]:
            return json.dumps(basic_settings())
        if command[-1]=="/proc/net/tcp":
            return TCP_HEADER
        if command[-1]=="/proc/net/tcp6":
            return TCP_HEADER+proc_line("127.0.0.1")
        raise AssertionError("unexpected command")
    monkeypatch.setattr(gate,"_call",fake_call)
    with pytest.raises(gate.BackupHold,match="changed_during_readback"):
        gate.collect(IMAGE,API_IMAGE)


def test_source_uses_fixed_query_without_secrets_or_mutation():
    s=SRC.read_text()
    assert "SHOW SETTINGS" in s
    assert "server.backup.listen_address" in s
    assert "server.backup.enabled" in s
    assert '["docker","exec","-i",API_CONTAINER,"python3","-c",' in s
    assert '["docker","exec",GRAPH_CONTAINER,"cat","/proc/net/tcp6"]' in s
    for disallowed in ("docker run","docker cp","docker restart",
                       "neo4j-admin database backup","docker exec neo4j bash",
                       "nft add","iptables -A","CREATE (t:Task)"):
        assert disallowed not in s


def test_cli_failed_evidence_always_redacted_and_hold(monkeypatch,capsys):
    def err(*_args):
        raise RuntimeError("secret_pass=DO_NOT_SHOW host.raw.local")
    monkeypatch.setattr(gate,"collect",err)
    rc=gate.main(["--expected-graph-image",IMAGE,
                 "--expected-api-image",API_IMAGE,"--json"])
    text=capsys.readouterr().out
    assert rc==3
    assert "DO_NOT_SHOW" not in text and "raw.local" not in text
    x=json.loads(text)
    assert x["production_decision"]=="HOLD"
    assert x["backup_run_authorized"] is False
    assert x["production_cutover_authorized"] is False


def test_no_application_routes_or_scheduled_jobs_reference_preflight():
    root=SRC.parents[1]
    for x in ("src/assistx/api.py","src/assistx/jobs.py",
              "src/assistx/routers/review.py"):
        assert "read_only_neo4j_backup_readiness" not in (root/x).read_text()


def test_no_required_args_prevents_run(monkeypatch):
    called=[]
    monkeypatch.setattr(gate,"collect",lambda *_:called.append(1))
    with pytest.raises(SystemExit) as e:
        gate.main([])
    assert e.value.code==2
    assert called==[]
