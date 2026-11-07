"""Synthetic, no-credentials contract for ephemeral LFM3 Docker witness."""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib import error, request

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/lfm3_docker_readonly_witness.py"
SPEC = importlib.util.spec_from_file_location("lfm3_witness", SOURCE)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class FakeReply:
    def __init__(self, obj, status=200):
        self.raw = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.status = status
    def __enter__(self):return self
    def __exit__(self, *_):return False
    def read(self, n):return self.raw[:n]


class FakeOpener:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []
    def open(self, req, timeout=0):
        self.calls.append((req.full_url, req.get_method(), timeout))
        return FakeReply(self.entries[req.full_url])


def make_fake(port=19181, *, slots=1, processing=0, model=None):
    name=model or m.EXPECTED_MODELS[port]
    url=f"http://127.0.0.1:{port}"
    return FakeOpener({
        url+"/health":{"status":"ok"},
        url+"/v1/models":{"data":[{"id":name}]},
        url+"/slots":[{"id":i,"is_processing":i<processing} for i in range(slots)],
    })


@pytest.mark.parametrize("port,count,busy",[(19180,4,0),(19180,4,2),(19181,1,0),(19181,1,1)])
def test_readiness_never_grants_capacity(port,count,busy):
    fake=make_fake(port,slots=count,processing=busy)
    result=m.read_witness(port,opener=fake)
    assert result['configured_slots']==count
    assert result['idle_slots']==count-busy
    assert result['capacity_approved'] is False
    assert result['task_admission']=='HOLD'
    assert result['physical_model_artifact_signed'] is False
    assert [x[1] for x in fake.calls]==['GET','GET','GET']
    assert all(x[2]==2.5 for x in fake.calls)


def test_mismatched_model_fails_closed():
    with pytest.raises(ValueError,match='model_or_slot_mismatch'):
        m.read_witness(19181,opener=make_fake(model='untrusted'))


@pytest.mark.parametrize('port',[22,1234,19000,19182])
def test_unapproved_upstream_never_contacted(port):
    with pytest.raises(ValueError,match='unapproved_model_port'):
        m.read_witness(port,opener=FakeOpener({}))


def test_slot_count_and_shape_cannot_be_assumed():
    empty=make_fake()
    empty.entries['http://127.0.0.1:19181/slots']=[]
    with pytest.raises(ValueError,match='model_or_slot_mismatch'):
        m.read_witness(19181,opener=empty)
    invalid=make_fake()
    invalid.entries['http://127.0.0.1:19181/slots']=[{'is_processing':'false'}]
    with pytest.raises(ValueError,match='slot_state_unresolved'):
        m.read_witness(19181,opener=invalid)


def test_oversized_upstream_holds():
    invalid=make_fake()
    invalid.entries['http://127.0.0.1:19181/slots']=b'x'*(m.MAX_BODY_BYTES+2)
    with pytest.raises(ValueError,match='upstream_oversized'):
        m.read_witness(19181,opener=invalid)


@pytest.mark.parametrize('ip',["0.0.0.0","127.0.0.1","169.254.0.1","8.8.8.8","::1"])
def test_public_or_loopback_bind_refused(ip):
    with pytest.raises(ValueError):
        m.private_interface_ipv4(ip)


def test_nonroot_run_contract_rejects_unbounded_arguments(monkeypatch):
    monkeypatch.setattr(m.os,'geteuid',lambda:1000)
    with pytest.raises(ValueError,match='unbounded_witness'):
        m.run_server('172.21.0.1','172.21.0.6',19182,19181,999)
    with pytest.raises(ValueError,match='invalid_port_or_model'):
        m.run_server('172.21.0.1','172.21.0.6',19182,33333,1)


def _http_exercise(monkeypatch, allowed_client):
    monkeypatch.setattr(m,'read_witness',lambda port: {
        'contract':'lfm3-docker-readonly-witness-v1',
        'model_alias':'synthetic-lfm3','task_admission':'HOLD',
        'capacity_approved':False, 'configured_slots':1,'idle_slots':1})
    with HTTPServer(('127.0.0.1',0),m.WitnessHandler) as server:
        server.allowed_client=allowed_client
        server.upstream_port=19181
        server.requests_seen=0
        service=threading.Thread(target=lambda:[server.handle_request() for _ in range(3)],daemon=True)
        service.start()
        port=server.server_address[1]
        def hit(method,path,headers=None):
            req=request.Request(f'http://127.0.0.1:{port}'+path,method=method,headers=headers or {},data=b'{}' if method=='POST' else None)
            client=request.build_opener(request.ProxyHandler({}),m.NoRedirect())
            try:
                with client.open(req,timeout=3) as r:
                    return r.status,json.load(r)
            except error.HTTPError as ex:
                return ex.code,json.load(ex)
        first=hit('GET','/readiness')
        second=hit('POST','/v1/chat/completions')
        third=hit('GET','/other?access_token=synthetic-secret')
        service.join(timeout=3)
        assert not service.is_alive()
        assert server.requests_seen==3
    return first,second,third


def test_real_http_only_readiness_no_post_or_generic_proxy(monkeypatch):
    first,second,third=_http_exercise(monkeypatch,'127.0.0.1')
    assert first[0]==200
    assert first[1]['task_admission']=='HOLD'
    assert second[0]==405 and second[1]['task_admission']=='HOLD'
    assert third[0]==404 and third[1]['task_admission']=='HOLD'


def test_real_http_rejects_wrong_source_ip_even_with_spoofed_headers(monkeypatch):
    first,second,third=_http_exercise(monkeypatch,'172.21.0.6')
    assert first[0]==403 and first[1]['reason']=='unapproved_client'
    assert second[0]==405
    assert third[0]==403


def test_redirect_disabled():
    assert m.NoRedirect().redirect_request(None,None,302,'go',{},'https://external.invalid') is None


def _fake_bridge_interface(*, name="br-synthetic", gateway="172.21.0.1", prefix=16):
    return [{"ifname": name, "addr_info": [
        {"family": "inet", "local": gateway, "prefixlen": prefix}
    ]}]


def test_bridge_interface_and_client_subnet_proof():
    m.verify_docker_bridge("172.21.0.1", "172.21.0.6", _fake_bridge_interface())


@pytest.mark.parametrize("name", ["eth0", "tailscale0", "wlan0", "veth0"])
def test_cannot_bind_private_non_docker_interfaces(name):
    with pytest.raises(ValueError, match="not_a_matching_private_docker_bridge"):
        m.verify_docker_bridge("172.21.0.1", "172.21.0.6",
                               _fake_bridge_interface(name=name))


def test_client_must_share_docker_interface_subnet():
    with pytest.raises(ValueError, match="not_a_matching_private_docker_bridge"):
        m.verify_docker_bridge("172.21.0.1", "172.22.0.6", _fake_bridge_interface())


def test_client_cannot_equal_gateway():
    with pytest.raises(ValueError, match="source_must_differ_from_gateway"):
        m.verify_docker_bridge("172.21.0.1", "172.21.0.1", _fake_bridge_interface())


def test_fake_health_response_not_accepted():
    fake=make_fake()
    fake.entries['http://127.0.0.1:19181/health']={'status':'not-ok'}
    with pytest.raises(ValueError, match="upstream_not_healthy"):
        m.read_witness(19181,opener=fake)


def test_malformed_health_status_is_controlled_hold():
    fake = make_fake()
    fake.entries["http://127.0.0.1:19181/health"] = {"status": []}
    with pytest.raises(ValueError, match="upstream_not_healthy"):
        m.read_witness(19181, opener=fake)


@pytest.mark.parametrize("addresses", [None, "not-addresses", {}])
def test_malformed_bridge_address_list_is_controlled_hold(addresses):
    with pytest.raises(ValueError, match="not_a_matching_private_docker_bridge"):
        m.verify_docker_bridge("172.21.0.1", "172.21.0.6",
                               [{"ifname": "br-synthetic", "addr_info": addresses}])


@pytest.mark.parametrize("prefix", [True, -1, 0, 33, "16"])
def test_invalid_docker_bridge_prefix_is_controlled_hold(prefix):
    with pytest.raises(ValueError, match="not_a_matching_private_docker_bridge"):
        m.verify_docker_bridge("172.21.0.1", "172.21.0.6",
                               _fake_bridge_interface(prefix=prefix))


def test_bad_network_interface_read_fails_before_bind(monkeypatch):
    monkeypatch.setattr(m.os, 'geteuid', lambda: 1000)
    def broken(*args, **kwargs):
        raise FileNotFoundError("synthetic missing ip binary")
    monkeypatch.setattr(m.subprocess, 'run', broken)
    with pytest.raises(ValueError, match="docker_bridge_identity_unverified"):
        m.run_server('172.21.0.1','172.21.0.6',19182,19181,1)
