"""Research-only source tests for three pinned native privilege inspections."""
from __future__ import annotations

import json

import pytest

from scripts import read_only_fleet_graph_principals as gate


A="sha256:"+"a"*64
B="sha256:"+"b"*64
C="sha256:"+"c"*64
PINS={
 "assistx-api":A,
 "assistx-worker":B,
 "assistx-hermes-adapter":C,
}


def native_result(image, *, admin=True, writes=2, wildcards=18,roles=2):
    return {
        "evidence_tier":"PRODUCTION_READ_ONLY_NATIVE_NEO4J",
        "container_id":"f"*64,
        "image_id":image,
        "native_query_scope":"CURRENT_USER_PRIVILEGES_READ_ONLY",
        "granted_privilege_count":19,
        "wildcard_graph_grant_count":wildcards,
        "write_action_count":writes,
        "role_count":roles,
        "has_admin_role":admin,
        "no_mutations":True,
        "production_decision":"HOLD",
        "promotion_authorized":False,
        "effective_privilege_cutover_verified":False,
    }


def fake_collect(monkeypatch, admin=True,writes=2,wildcards=18, tamper=None):
    names=[]
    previous=gate.native.TARGET
    def collect(*,expected_image):
        names.append(gate.native.TARGET)
        item=native_result(expected_image,admin=admin,writes=writes,wildcards=wildcards)
        if tamper:
            item.update(tamper)
        return item
    monkeypatch.setattr(gate.native,"collect",collect)
    return names,previous


def test_three_exact_allowlisted_services_inspected_sequentially(monkeypatch):
    names,original=fake_collect(monkeypatch)
    summary=gate.census(pinned_images=PINS)
    assert names==["assistx-api","assistx-worker","assistx-hermes-adapter"]
    assert gate.native.TARGET==original
    assert summary["broad_admin_service_count"]==3
    assert summary["broad_write_service_count"]==3
    assert summary["wildcard_graph_service_count"]==3
    assert summary["roles_cutover_verified"] is False
    assert summary["global_task_writer_authority_verified"] is False
    assert summary["dispatch_authorized"] is False
    assert summary["production_decision"]=="HOLD"
    assert [x["service"] for x in summary["services"]]==names
    assert "container_id" not in str(summary)
    assert "password" not in str(summary).lower()


def test_even_restricted_metadata_never_self_authorizes_dispatch(monkeypatch):
    fake_collect(monkeypatch,admin=False,writes=0,wildcards=0)
    summary=gate.census(pinned_images=PINS)
    assert summary["broad_admin_service_count"]==0
    assert summary["broad_write_service_count"]==0
    assert summary["production_decision"]=="HOLD"
    assert summary["dispatch_authorized"] is False
    assert summary["roles_cutover_verified"] is False


@pytest.mark.parametrize("bad_pins",[
  {}, {"assistx-api":A}, {**PINS,"other-container":A},
  {**PINS,"assistx-api":"sha256:abcd"},
  {**PINS,"assistx-api":None},
  {**PINS,"assistx-worker":"sha256:"+"z"*64},
])
def test_invalid_target_or_image_fails_before_docker(monkeypatch,bad_pins):
    names,_=fake_collect(monkeypatch)
    with pytest.raises(gate.CensusHold):
        gate.census(pinned_images=bad_pins)
    assert names==[]


@pytest.mark.parametrize("tamper",[
    {"production_decision":"GO"},
    {"promotion_authorized":True},
    {"effective_privilege_cutover_verified":True},
    {"no_mutations":False},
    {"unexpected_secret":"raw"},
    {"image_id":"sha256:"+"0"*64},
])
def test_untrusted_native_receipt_refused(monkeypatch,tamper):
    names,original=fake_collect(monkeypatch,tamper=tamper)
    with pytest.raises(gate.CensusHold,match="unexpected_native"):
        gate.census(pinned_images=PINS)
    assert len(names)==1
    assert gate.native.TARGET==original


def test_native_error_is_redacted_and_runtime_target_restored(monkeypatch,capsys):
    original=gate.native.TARGET
    def broken(*,expected_image):
        raise gate.native.CensusHold("password=DO_NOT_EXPOSE")
    monkeypatch.setattr(gate.native,"collect",broken)
    rc=gate.main([
      "--expected-api-image",A,"--expected-worker-image",B,
      "--expected-hermes-image",C,"--json",
    ])
    result=json.loads(capsys.readouterr().out)
    assert rc==3
    assert result["production_decision"]=="HOLD"
    assert "DO_NOT_EXPOSE" not in str(result)
    assert gate.native.TARGET==original


def test_broad_runtime_receipt_returns_failing_gate(monkeypatch,capsys):
    fake_collect(monkeypatch)
    assert gate.main([
      "--expected-api-image",A,"--expected-worker-image",B,
      "--expected-hermes-image",C,"--json",
    ])==3
    result=json.loads(capsys.readouterr().out)
    assert result["all_required_services_inspected"] is True
    assert result["broad_admin_service_count"]==3
    assert result["dispatch_authorized"] is False


def test_missing_cli_pins_never_reads_docker(monkeypatch):
    names,_=fake_collect(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        gate.main(["--expected-api-image",A,"--json"])
    assert exc.value.code==2
    assert names==[]


def test_static_policy_no_mutation_or_automatic_job():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code=(root/"scripts/read_only_fleet_graph_principals.py").read_text()
    for phrase in ("CREATE USER ","GRANT ROLE ","REVOKE ","CREATE (",
                   "execute_write(","run_task(","docker run "):
        assert phrase not in code
    for p in ("src/assistx/api.py","src/assistx/jobs.py","src/assistx/routers/review.py"):
        assert "read_only_fleet_graph_principals" not in (root/p).read_text()
