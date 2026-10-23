"""Static attestation remains fail-closed before and after cutover."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/assistx_tailnet_postcutover_attestation.py"
SPEC=importlib.util.spec_from_file_location("assistx_postcutover_attestation_test",SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

CANDIDATE=dict(
    legacy_active=False,legacy_boot_enabled=False,
    caddy_active=True,caddy_boot_enabled=True,
    caddy_process_pinned=True,caddy_config_pinned=True,
    caddy_binary_pinned=True,tailnet_listener=True,
    localhost_listener=True,unexpected_listener=False,
    tailnet_socket_owned_by_caddy=True,mobile_serve_scope_exact=True,
)

def evaluate(**kwargs):
    return mod.assess(**{**CANDIDATE,**kwargs})


def test_default_inert():
    output=subprocess.run([sys.executable,str(SCRIPT)],check=True,
                          capture_output=True,text=True,timeout=8)
    report=json.loads(output.stdout)
    assert report["decision"]=="HOLD"
    assert report["physical_executed"] is False
    assert report["production_mutations"]==0


def test_even_static_perfect_and_offhost_auth_never_production_go():
    for client in (False,True):
        report=evaluate(real_offhost_auth_verified=client)
        assert report["postcutover_static_gate_met"] is True
        assert report["tailnet_socket_owned_by_caddy"] is True
        assert report["decision"]=="HOLD"
        assert report["safe_fallback_activated_and_qualified"] is False
        assert report["production_release_authorized"] is False


@pytest.mark.parametrize("fault",[
    {"legacy_active":True},{"legacy_boot_enabled":True},
    {"caddy_active":False},{"caddy_boot_enabled":False},
    {"caddy_process_pinned":False},{"caddy_config_pinned":False},
    {"caddy_binary_pinned":False},{"tailnet_listener":False},
    {"localhost_listener":False},{"unexpected_listener":True},
    {"tailnet_socket_owned_by_caddy":False},{"mobile_serve_scope_exact":False},
])
def test_every_static_regression_fails_closed(fault):
    report=evaluate(**fault)
    assert report["postcutover_static_gate_met"] is False
    assert report["decision"]=="HOLD"
    assert report["production_mutations"]==0


def test_nonboolean_inputs_fail_closed():
    for field in CANDIDATE:
        with pytest.raises(mod.Unsafe):
            evaluate(**{field:"false"})


def test_missing_binary_or_config_fails_pin(tmp_path):
    assert mod.is_pinned_file(tmp_path/"absent",mod.EXPECTED_CONFIG_SHA) is False
    target=tmp_path/"config"
    target.write_text("test")
    assert mod.is_pinned_file(target,mod.EXPECTED_CONFIG_SHA) is False
    link=tmp_path/"symlink"
    link.symlink_to(target)
    assert mod.is_pinned_file(link,mod.EXPECTED_CONFIG_SHA) is False


def test_process_rejects_invalid_identity():
    for pid in (-1,0,4194305):
        assert mod.process_identity(pid) is False


def test_socket_owner_checks_exact_uid_inode_cgroup_and_bind():
    addr="100.64.43.123:8000"
    unit="/user.slice/user-1000.slice/user@1000.service/app.slice/assistx-tailnet-sanitizer.service"
    good=f"LISTEN 0 5 {addr} 0.0.0.0:* uid:1000 ino:83254 sk:7e cgroup:{unit} <->"
    assert mod.service_owns_listener(good,addr,unit) is True
    assert mod.service_owns_listener(good,addr,unit,uid=1001) is False
    assert mod.service_owns_listener(good,addr,unit+"-wrong") is False
    assert mod.service_owns_listener(good,"127.0.0.1:8000",unit) is False
    assert mod.service_owns_listener(good+"\n"+good,addr,unit) is False
    for change in ("uid:1000", "ino:83254", "cgroup:"+unit):
        assert mod.service_owns_listener(good.replace(change,""),addr,unit) is False
    assert mod.service_owns_listener(good.replace("ino:83254","ino:0"),addr,unit) is False
    assert mod.service_owns_listener(good.replace("ino:83254","ino:oops"),addr,unit) is False
    assert mod.service_owns_listener(good.replace("uid:1000","uid:1001"),addr,unit) is False
    assert mod.service_owns_listener(good.replace("cgroup:"+unit,"cgroup:/system.slice/docker.service"),addr,unit) is False
    assert mod.service_owns_listener(good.replace(addr,"0.0.0.0:8000"),addr,unit) is False


def test_mismatched_socket_owner_never_reaches_static_gate():
    result=evaluate(tailnet_socket_owned_by_caddy=False)
    assert result["postcutover_static_gate_met"] is False
    assert result["tailnet_socket_owned_by_caddy"] is False
    assert result["production_release_authorized"] is False


def test_mobile_serve_dns_is_exact_and_scope_bounded():
    hostname="x1-370.tailcb8954.ts.net"
    exact={path:{"Proxy":"http://127.0.0.1:8000"+path}
           for path in mod.MOBILE_PATHS}
    other={"/":{"Proxy":"http://127.0.0.1:8081"}}
    good={hostname+":8443":{"Handlers":{**exact,**other}},
          hostname+":8445":{"Handlers":other}}
    assert mod.mobile_serve_paths_exact(good,hostname) is True
    assert mod.mobile_serve_paths_exact(good,"untrusted.local") is False
    bad={"attacker.ts.net:8443":{"Handlers":exact},
         hostname+":8443":{"Handlers":other}}
    assert mod.mobile_serve_paths_exact(bad,hostname) is False
    for path in mod.MOBILE_PATHS:
        missing={hostname+":8443":{"Handlers":{
            name:value for name,value in exact.items() if name!=path}}}
        assert mod.mobile_serve_paths_exact(missing,hostname) is False
    bad_root={hostname+":8443":{"Handlers":{**exact,
         "/api/tasks":{"Proxy":"http://127.0.0.1:8000/api/tasks"}}}}
    assert mod.mobile_serve_paths_exact(bad_root,hostname) is False
    mismatched={hostname+":8443":{"Handlers":{**exact,
         "/api/v1/auth/whoami":{"Proxy":"http://127.0.0.1:8100/other"}}}}
    assert mod.mobile_serve_paths_exact(mismatched,hostname) is False


def test_current_raw_socket_not_misidentified_as_candidate():
    raw=("LISTEN 0 5 100.64.43.123:8000 0.0.0.0:* "
         "uid:1000 ino:83254 sk:7e "
         "cgroup:/user.slice/user-1000.slice/user@1000.service/app.slice/control-room-ts-proxy.service <->")
    new="/user.slice/user-1000.slice/user@1000.service/app.slice/assistx-tailnet-sanitizer.service"
    assert mod.service_owns_listener(raw,"100.64.43.123:8000",new) is False


def test_no_mutating_commands_or_raw_socat_recovery():
    content=SCRIPT.read_text()
    assert '"systemctl","--user","is-active"' in content
    assert '"systemctl","--user","is-enabled"' in content
    assert "str(EXPECTED_CONFIG)" in content
    assert 'sha256' in content
    assert "safe_fallback_activated_and_qualified" in content
    for forbidden in ("docker restart","systemctl stop","systemctl disable",
                      "systemctl restart","tailscale serve --bg",
                      "socat TCP-LISTEN","method=\"POST\"","get_basic_in_memory"):
        assert forbidden not in content
