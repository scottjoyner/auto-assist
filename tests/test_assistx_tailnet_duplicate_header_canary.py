"""Fail-closed tests for duplicate/case-variant untrusted identity headers."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import assistx_tailnet_duplicate_header_canary as canary
import pytest


def test_default_inert():
    r=subprocess.run([sys.executable,str(Path(canary.__file__))],
                     check=True,capture_output=True,text=True,timeout=8)
    state=json.loads(r.stdout)
    assert state["decision"]=="HOLD"
    assert state["physical_executed"] is False
    assert state["production_cutover_authorized"] is False


def test_cases_include_duplicates_case_variants_and_request_methods():
    cases=canary.cases()
    assert {method for method,_ in cases}=={"GET","POST","PUT"}
    assert len(cases)>=5
    assert any(len(pairs)!=len({name.lower() for name,_ in pairs})
               for _,pairs in cases)
    assert any(name!=name.title()
               for _,pairs in cases for name,_ in pairs)
    assert {name.lower() for _,pairs in cases for name,_ in pairs}=={
        name.lower() for name in canary.DROPPED_HEADERS}


@pytest.mark.parametrize("method",["DELETE","PATCH","HEAD","TRACE","OPTIONS"])
def test_methods_not_in_synthetic_allowlist(method):
    with pytest.raises(canary.Denied):
        canary.raw_probe(20001,method,())


@pytest.mark.parametrize("port",[0,1,1025,8000,65536])
def test_production_or_invalid_ports_refused(port):
    with pytest.raises(canary.Denied):
        canary.raw_probe(port,"GET",())


def test_refuse_injecting_unexpected_headers():
    with pytest.raises(canary.Denied):
        canary.raw_probe(22221,"GET",(("Host","example.invalid"),))


def test_backend_detects_duplicate_instances_using_get_all():
    source=Path(canary.__file__).read_text()
    assert 'self.headers.get_all(name, [])' in source
    assert 'identity_header_instances' in source
    assert 'len(basic)==1' in source


def test_live_services_and_credentials_are_out_of_scope():
    source=Path(canary.__file__).read_text()
    assert "staged_caddyfile(backend_port,front)" in source
    assert '("127.0.0.1",0)' in source
    assert "ThreadingHTTPServer" in source
    assert '"production_network_requests":0' in source
    assert '"production_service_mutations":0' in source
    assert '"production_cutover_authorized":False' in source
    for phrase in ('systemctl stop','docker restart','docker exec',
                   'tailscale serve','get_basic_in_memory','100.64.43.123',
                   'method="POST"'):
        assert phrase not in source
