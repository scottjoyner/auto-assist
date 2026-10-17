"""Fail-closed fixed native SHOW privilege census. No real Bolt/Docker in tests."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/read_only_native_graph_privileges.py"
SPEC = importlib.util.spec_from_file_location("readonly_graph_census_under_test", MODULE)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64
SECRET = "synthetic-not-a-credential"

def fake_obj(image=IMAGE, container=CONTAINER):
    return {
        "Name": "/assistx-api",
        "Id": container,
        "Image": image,
        "State": {"Status": "running"},
        "Config": {"Env": ["NEO4J_USER=" + SECRET, "NEO4J_PASSWORD=" + SECRET]},
    }

def native(admin=True, wildcard=18, write=2):
    return {
        "native_query_scope": "CURRENT_USER_PRIVILEGES_READ_ONLY",
        "granted_privilege_count": 19,
        "wildcard_graph_grant_count": wildcard,
        "write_action_count": write,
        "role_count": 2,
        "has_admin_role": admin,
        "no_mutations": True,
    }

def mock_reads(monkeypatch, images=None, values=None):
    observed = []
    versions = list(images or [fake_obj(), fake_obj()])
    value = values if values is not None else native()
    def query(args, timeout=9):
        observed.append(args)
        if args[:2] == ["container", "inspect"]:
            obj = versions.pop(0)
            return json.dumps([obj])
        if args[:2] == ["exec", "-i"]:
            return json.dumps(value)
        raise AssertionError("unexpected Docker request")
    monkeypatch.setattr(audit, "_docker", query)
    return observed


def test_runtime_readback_reports_admin_and_stays_hold(monkeypatch):
    ops = mock_reads(monkeypatch)
    result = audit.collect(expected_image=IMAGE)
    assert result["evidence_tier"] == "PRODUCTION_READ_ONLY_NATIVE_NEO4J"
    assert result["production_decision"] == "HOLD"
    assert result["promotion_authorized"] is False
    assert result["effective_privilege_cutover_verified"] is False
    assert result["has_admin_role"] is True
    assert result["wildcard_graph_grant_count"] == 18
    assert result["write_action_count"] == 2
    assert SECRET not in json.dumps(result)
    assert ops[0] == ["container", "inspect", "assistx-api"]
    assert ops[1][0:4] == ["exec", "-i", "assistx-api", "python"]
    assert ops[2] == ops[0]


def test_only_two_pinned_readonly_queries_in_probe():
    text = audit.CONTAINER_PROBE
    assert 'with driver.session(database="system",default_access_mode=READ_ACCESS)' in text
    assert text.count("session.run(") == 2
    assert '"SHOW USER PRIVILEGES"' in text
    assert '"SHOW CURRENT USER YIELD roles RETURN roles"' in text
    assert "NEO4J_PASSWORD" in text
    assert "print(json.dumps(report" in text
    assert "driver.close()" in text


@pytest.mark.parametrize("bad", [
    "", "latest", "sha256:bad", "sha256:" + "Z" * 64,
])
def test_image_id_must_be_immutable_and_pinned_before_docker(monkeypatch, bad):
    monkeypatch.setattr(
        audit, "_docker",
        lambda *a, **k: pytest.fail("Docker cannot run for invalid image"),
    )
    with pytest.raises(audit.CensusHold, match="pin"):
        audit.collect(expected_image=bad)


def test_image_mismatch_disables_exec(monkeypatch):
    ops=[]
    def fake(args, timeout=9):
        ops.append(args)
        if args[:2] != ["container", "inspect"]:
            pytest.fail("unexpected native auth access")
        return json.dumps([fake_obj(image="sha256:" + "0"*64)])
    monkeypatch.setattr(audit, "_docker", fake)
    with pytest.raises(audit.CensusHold, match="image mismatch"):
        audit.collect(expected_image=IMAGE)
    assert len(ops)==1


def test_runtime_replacement_during_native_query_denied(monkeypatch):
    objs=[fake_obj(), fake_obj(container="d"*64)]
    mock_reads(monkeypatch, images=objs)
    with pytest.raises(audit.CensusHold, match="drift"):
        audit.collect(expected_image=IMAGE)


@pytest.mark.parametrize("corrupt", [
    {"Name":"/neo4j"},
    {"State":{"Status":"exited"}},
    {"Id":"not-a-container-id"},
])
def test_wrong_runtime_container_is_rejected(monkeypatch, corrupt):
    obj=fake_obj()
    obj.update(corrupt)
    ops=mock_reads(monkeypatch, images=[obj])
    with pytest.raises(audit.CensusHold):
        audit.collect(expected_image=IMAGE)
    assert len(ops)==1


@pytest.mark.parametrize("bad", [
    {"has_admin_role":"true"},
    {"write_action_count":True},
    {"granted_privilege_count":-1},
    {"wildcard_graph_grant_count":21},
    {"query":"CALL dbms.queryJmx()"},
    {"no_mutations":False},
    {"native_query_scope":"UNVERIFIED"},
    {"secret":"synthetic-sensitive"},
])
def test_unexpected_native_receipt_never_accepted(monkeypatch, bad):
    data=native()
    data.update(bad)
    mock_reads(monkeypatch, values=data)
    with pytest.raises(audit.CensusHold):
        audit.collect(expected_image=IMAGE)


def test_no_silent_success_on_docker_unavailable(monkeypatch):
    monkeypatch.setattr(
        audit.subprocess,"run",
        lambda *a,**k: SimpleNamespace(returncode=1, stdout="", stderr=SECRET),
    )
    with pytest.raises(audit.CensusHold) as err:
        audit._docker(["container","inspect","assistx-api"])
    assert SECRET not in str(err.value)


def test_actual_docker_invocation_fixed_argv_timeout(monkeypatch):
    seen=[]
    def fake(cmd,**kwargs):
        seen.append((cmd,kwargs))
        return SimpleNamespace(returncode=0,stdout="[]",stderr="")
    monkeypatch.setattr(audit.subprocess,"run",fake)
    assert audit._docker(["container","inspect","assistx-api"])=="[]"
    cmd,kw=seen[0]
    assert cmd==["docker","container","inspect","assistx-api"]
    assert kw["timeout"]==9
    assert kw["capture_output"] is True


def test_cli_reports_hold_on_broad_privileges(monkeypatch,capsys):
    mock_reads(monkeypatch)
    assert audit.main(["--expected-image",IMAGE,"--json","--deny-on-broad-privilege"])==3
    val=json.loads(capsys.readouterr().out)
    assert val["has_admin_role"] is True
    assert SECRET not in json.dumps(val)


def test_cli_narrow_evidence_still_never_auto_promotes(monkeypatch,capsys):
    mock_reads(monkeypatch,values=native(admin=False,wildcard=0,write=0))
    assert audit.main(["--expected-image",IMAGE,"--json","--deny-on-broad-privilege"])==0
    val=json.loads(capsys.readouterr().out)
    assert val["production_decision"]=="HOLD"
    assert val["promotion_authorized"] is False


def test_invalid_docker_response_is_redacted(monkeypatch,capsys):
    monkeypatch.setattr(audit, "_docker",lambda *a,**k: SECRET)
    assert audit.main(["--expected-image",IMAGE,"--json"])==3
    val=json.loads(capsys.readouterr().out)
    assert SECRET not in json.dumps(val)


def test_large_docker_inspect_is_accepted_but_large_native_output_denied(monkeypatch):
    def fake(cmd, **kwargs):
        if cmd[1:3] == ["container", "inspect"]:
            return SimpleNamespace(returncode=0,stdout="x" * 15000,stderr="")
        return SimpleNamespace(returncode=0,stdout="x" * 15000,stderr="")
    monkeypatch.setattr(audit.subprocess, "run", fake)
    assert len(audit._docker(["container","inspect","assistx-api"])) == 15000
    with pytest.raises(audit.CensusHold, match="oversized"):
        audit._docker(["exec","-i","assistx-api","python","-c",audit.CONTAINER_PROBE])


def test_oversized_container_inspection_is_also_denied(monkeypatch):
    monkeypatch.setattr(audit.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="x" * 300000, stderr=""))
    with pytest.raises(audit.CensusHold, match="oversized"):
        audit._docker(["container","inspect","assistx-api"])
