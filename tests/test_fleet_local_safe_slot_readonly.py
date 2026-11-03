"""Synthetic fleet resource admission advice. Never runs inference or alters a lease."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fleet_local_safe_slot_readonly.py"
spec = importlib.util.spec_from_file_location("fleet_safe_slot", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NONCE = "a1" * 16


def sample(**changes):
    data = {
        "ram_mib": 20000, "swap_mib": 5000, "root_gib": 70.0,
        "mem_full_avg10": 0.0, "io_full_avg10": 0.0, "io_full_avg60": 0.0,
    }
    data.update(changes)
    return data


def response(host, *, one=None, two=None, catalog=None):
    return {
        "sample1": sample() if one is None else one,
        "sample2": sample() if two is None else two,
        "catalog": (
            {"available": True, "resident": True, "reasoning_off_supported": True}
            if catalog is None else catalog
        ),
        "mode": host, "nonce": NONCE,
    }


def assert_hold(row):
    assert row["capacity_status"] == "HOLD"
    assert row["can_run_scheduled_research"] is False
    assert row["can_dispatch_production"] is False
    assert row["existing_xwing_manual_quality_hold_preserved"] is True


def test_xwing_resident_healthy_is_only_manual_requalification():
    row = module.evaluate("xwing", response("xwing"))
    assert row["capacity_status"] == "MANUAL_SHADOW_REQUALIFICATION_ONLY"
    assert row["hold_reasons"] == []
    assert row["qualification_reasons"] == ["scheduled_xwing_quality_stop_preserved"]
    assert row["can_run_scheduled_research"] is False
    assert row["can_dispatch_production"] is False


def test_lenovo_catalog_only_is_not_loaded_model_proof():
    row = module.evaluate("lenovo", response("lenovo", catalog={
        "available": True, "resident": False, "reasoning_off_supported": False,
    }))
    assert row["capacity_status"] == "MANUAL_QUALITY_QUALIFICATION_ONLY"
    assert "advertised_model_not_physically_attested" in row["qualification_reasons"]
    assert "recent_output_format_quality_failures" in row["qualification_reasons"]
    assert row["catalog_resident_claim"] is False
    assert row["can_dispatch_production"] is False


def test_deathstar_installed_ollama_can_only_attempt_manual_quality():
    row = module.evaluate("deathstar", response("deathstar", catalog={
        "available": True, "resident": False, "reasoning_off_supported": False,
    }))
    assert row["capacity_status"] == "MANUAL_QUALITY_QUALIFICATION_ONLY"
    assert "ollama_requires_transient_manual_model_load" in row["qualification_reasons"]
    assert "recent_small_model_quality_failures" in row["qualification_reasons"]
    assert row["can_run_scheduled_research"] is False


def test_x1_no_dispatch_even_if_resources_and_catalog_apparently_good():
    row = module.evaluate("x1-370", response("x1-370"))
    assert_hold(row)
    assert "gpu_lease_and_canonical_hold_not_evaluated" in row["hold_reasons"]


def test_destroyer_no_assumed_endpoint_even_if_all_resources_green():
    row = module.evaluate("destroyer", response("destroyer"))
    assert_hold(row)
    assert "no_reviewed_active_model_endpoint" in row["hold_reasons"]


@pytest.mark.parametrize(("host","name","value","reason"),[
    ("xwing", "ram_mib", 2499, "ram_below_manual_floor"),
    ("xwing", "swap_mib", 1023, "swap_below_manual_floor"),
    ("xwing", "root_gib", 4.99, "root_disk_below_floor"),
    ("xwing", "mem_full_avg10", 1.0, "memory_pressure"),
    ("xwing", "io_full_avg10", 5.0, "io_pressure"),
    ("xwing", "io_full_avg60", 5.0, "io_pressure"),
    ("deathstar", "ram_mib", 4095, "ram_below_manual_floor"),
    ("lenovo", "ram_mib", 2999, "ram_below_manual_floor"),
    ("joyner", "ram_mib", 1272, "ram_below_manual_floor"),
])
def test_any_of_two_samples_below_threshold_blocks(host,name,value,reason):
    row = module.evaluate(host,response(host,two=sample(**{name:value})))
    assert_hold(row)
    assert reason in row["hold_reasons"]


def test_xwing_missing_resident_model_or_reasoning_off_holds():
    for cat in (
        {"available":True,"resident":False,"reasoning_off_supported":True},
        {"available":True,"resident":True,"reasoning_off_supported":False},
    ):
        row=module.evaluate("xwing",response("xwing",catalog=cat))
        assert_hold(row)


def test_missing_catalog_blocks_even_with_safe_resources():
    row=module.evaluate("deathstar",response("deathstar",catalog={
        "available":False,"resident":False,"reasoning_off_supported":False,
    }))
    assert_hold(row)
    assert "reviewed_model_not_advertised" in row["hold_reasons"]


def test_live_snapshot_xwing_resource_hold():
    row=module.evaluate("xwing",response("xwing",one=sample(
        ram_mib=2032,mem_full_avg10=2.43,io_full_avg10=5.35,
    )))
    assert_hold(row)
    assert {"ram_below_manual_floor","memory_pressure","io_pressure"} <= set(row["hold_reasons"])


def test_live_snapshot_x1_swap_and_reservation_hold():
    row=module.evaluate("x1-370",response("x1-370",one=sample(swap_mib=6)))
    assert_hold(row)
    assert "swap_below_manual_floor" in row["hold_reasons"]


def test_live_snapshot_joyner_ram_swap_hold():
    row=module.evaluate("joyner",response("joyner",one=sample(ram_mib=1272,swap_mib=3)))
    assert_hold(row)
    assert {"swap_below_manual_floor","ram_below_manual_floor"}<=set(row["hold_reasons"])


def test_live_snapshot_deathstar_io_pressure_holds():
    row=module.evaluate("deathstar",response("deathstar",one=sample(io_full_avg10=8.62,io_full_avg60=7.17)))
    assert_hold(row)
    assert "io_pressure" in row["hold_reasons"]


def test_remote_json_nonce_and_mode_binding():
    out=module.parse_remote(json.dumps(response("xwing")),host="xwing",nonce=NONCE)
    assert out["mode"]=="xwing"
    with pytest.raises(ValueError,match="wrong_nonce_or_mode"):
        module.parse_remote(json.dumps(response("xwing")),host="xwing",nonce="f"*32)
    with pytest.raises(ValueError,match="wrong_nonce_or_mode"):
        module.parse_remote(json.dumps(response("xwing")),host="lenovo",nonce=NONCE)


@pytest.mark.parametrize("raw",[
    "", "{}", "[]", '{"sample1":1,"sample1":2}', "x"*2049,
])
def test_bad_remote_data_does_not_authorize(raw):
    with pytest.raises(ValueError):
        module.parse_remote(raw,host="xwing",nonce=NONCE)


@pytest.mark.parametrize(("field","value"),[
    ("ram_mib",True),
    ("swap_mib",-1),
    ("mem_full_avg10",float("nan")),
    ("io_full_avg10",float("inf")),
    ("root_gib","9.0"),
    ("ram_mib",10000000000),
])
def test_resource_numbers_must_be_typed_finite_and_bounded(field,value):
    out=response("xwing")
    out["sample1"][field]=value
    with pytest.raises(ValueError):
        module.parse_remote(json.dumps(out),host="xwing",nonce=NONCE)


def test_extra_remote_authority_field_refused():
    out=response("xwing")
    out["can_dispatch_production"]=True
    with pytest.raises(ValueError,match="untrusted_remote_schema"):
        module.parse_remote(json.dumps(out),host="xwing",nonce=NONCE)


def test_fake_catalog_types_refused():
    out=response("xwing")
    out["catalog"]["resident"]=1
    with pytest.raises(ValueError,match="invalid_catalog_type"):
        module.parse_remote(json.dumps(out),host="xwing",nonce=NONCE)


def test_unreviewed_host_never_runs_ssh():
    called=[]
    out=module.probe("evil;python -c attack",
        runner=lambda *args,**kw:called.append(args),nonce_source=lambda n:b"a"*n)
    assert out["capacity_status"]=="HOLD"
    assert called==[]


def test_ssh_strict_host_and_no_auth_forwarding():
    calls=[]
    def runner(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps(response("xwing")),
                               stderr="SECRET")
    row=module.probe("xwing",runner=runner,nonce_source=lambda n:bytes.fromhex(NONCE))
    assert row["capacity_status"]=="MANUAL_SHADOW_REQUALIFICATION_ONLY"
    cmd,kwargs=calls[0]
    assert cmd[-2]=="xwing"
    for tag in ("BatchMode=yes","StrictHostKeyChecking=yes","ForwardAgent=no",
                "ClearAllForwardings=yes","ControlMaster=no","ControlPath=none"):
        assert tag in cmd
    assert kwargs["timeout"]==13 and kwargs["check"] is False
    assert "SECRET" not in str(row)


def test_failed_ssh_never_claims_ready():
    def runner(*args,**kwargs):
        return SimpleNamespace(returncode=255,stdout="",stderr="SECRET")
    result=module.probe("lenovo",runner=runner,nonce_source=lambda n:bytes.fromhex(NONCE))
    assert_hold(result)
    assert "SECRET" not in str(result)


def test_remote_source_is_fixed_readonly_and_no_inference():
    source=SCRIPT.read_text()
    for forbidden in ('method="POST"','models/load','models/unload',
                      'chat/completions','/api/generate','sudo ',
                      'shell=True','os.system(','systemctl restart',
                      'Neo4j','X-Fleet-Node-Token'):
        assert forbidden not in source
    compile(module.REMOTE,"<resource-check>","exec")


def test_cli_only_reports_no_dispatch_and_exits_nonzero(capsys):
    with patch.object(module,"probe",side_effect=[
        module.evaluate(host,response(host)) for host in module.ALIASES
    ]):
        assert module.main([])==2
    out=json.loads(capsys.readouterr().out)
    assert out["can_dispatch_production"] is False
    assert out["inference_requests"]==0
    assert out["paid_api_calls"]==0
    assert out["production_mutations"]==0
    assert "x1-370" not in out["technical_manual_candidates"]
    assert "xwing" in out["technical_manual_candidates"]


def test_missing_nonce_or_invalid_nonce_source_holds():
    row=module.probe("deathstar",nonce_source=lambda n:b"oops")
    assert_hold(row)
