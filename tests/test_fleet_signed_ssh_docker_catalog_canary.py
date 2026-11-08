"""Hermetic security regression coverage for the non-admitting SSH-Docker stage."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SRC))
spec = importlib.util.spec_from_file_location('fleet_ssh_signed', SRC/'fleet_signed_ssh_docker_catalog_canary.py')
assert spec and spec.loader
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
KEY=b'K'*32
NODE=b'{"node":"xwing"}'


def fake_interfaces():
    return [{'ifname':'br-synthetic','addr_info':[
        {'family':'inet','local':'172.21.0.1','prefixlen':16}]}]


@pytest.mark.parametrize('client', ['172.21.0.6','172.21.9.9'])
def test_docker_private_bridge_verified(client):
    m.validate_docker_bridge('172.21.0.1',client,fake_interfaces())


@pytest.mark.parametrize('bad_source', ['127.0.0.1','8.8.8.8','172.22.0.6','172.21.0.1'])
def test_wrong_source_refused(bad_source):
    with pytest.raises(ValueError):
        m.validate_docker_bridge('172.21.0.1',bad_source,fake_interfaces())


@pytest.mark.parametrize('interfaces', [
    [],[{'ifname':'eth0','addr_info':[{'family':'inet','local':'172.21.0.1','prefixlen':16}]}],
    [{'ifname':'br-fake','addr_info':None}],
    [{'ifname':'br-fake','addr_info':'not_a_list'}],
    [{'ifname':'br-fake','addr_info':[{'family':'inet','local':'172.21.0.1','prefixlen':True}]}],
    [{'ifname':'br-fake','addr_info':[{'family':'inet','local':'172.21.0.1','prefixlen':33}]}],
    [{'ifname':'br-fake','addr_info':[{'family':'inet','local':'172.21.0.2','prefixlen':16}]}],
])
def test_malformed_bridge_metadata_refused(interfaces):
    with pytest.raises(ValueError):
        m.validate_docker_bridge('172.21.0.1','172.21.0.6',interfaces)


def test_hmac_signature_roundtrip_and_replay():
    stamp = str(int(time.time()))
    nonce = 'a'*32
    sig = m.sign(KEY, stamp, nonce, NODE)
    seen = set()
    m.check_signature(KEY,stamp,nonce,sig,NODE,seen,int(stamp))
    assert seen == {nonce}
    with pytest.raises(ValueError,match='nonce_replayed'):
        m.check_signature(KEY,stamp,nonce,sig,NODE,seen,int(stamp))


@pytest.mark.parametrize('suf', ['0'*64,'A'*64,'xyz'])
def test_bad_signatures_rejected(suf):
    with pytest.raises(ValueError):
        m.check_signature(KEY,str(int(time.time())),'a'*32,suf,NODE,set(),int(time.time()))


def test_stale_signatures_refused():
    stamp=str(int(time.time())-100)
    with pytest.raises(ValueError,match='stale_or_invalid_signature'):
        m.check_signature(KEY,stamp,'a'*32,m.sign(KEY,stamp,'a'*32,NODE),NODE,set(),int(time.time()))


@pytest.mark.parametrize('body', [b'',b'{}',b'{"node":"outsider"}',b'{"node":123}',
                                b'{"node":"xwing","node":"joyner"}',
                                b'{"node":"xwing","task":"claim"}',
                                b'x'*65])
def test_only_fixed_catalog_identity_accepted(body):
    with pytest.raises(ValueError):
        m.parse_fixed_body(body)


def test_fixed_identity_and_no_request_payload():
    assert m.parse_fixed_body(b'{"node":"xwing"}')=='xwing'
    assert m.parse_fixed_body(b'{"node":"joyner"}')=='joyner'
    assert m.MAX_POST_BYTES==64
    assert m.MAX_REQUESTS<=7 and m.MAX_SECONDS<=30


def test_default_does_not_open_sockets(monkeypatch,capsys):
    monkeypatch.setattr(m,'serve',lambda *_a,**_k:pytest.fail('opened server'))
    assert m.main([])==2
    res=json.loads(capsys.readouterr().out)
    assert res['task_admission']=='HOLD' and res['approved_parallel_slots']==0


def test_live_missing_fd_fails_closed(monkeypatch,capsys):
    monkeypatch.setattr(m,'serve',lambda *_a,**_k:pytest.fail('opened server'))
    assert m.main(['--live','--listen-ip','172.21.0.1','--client-ip','172.21.0.6'])==2
    assert json.loads(capsys.readouterr().out)['status']=='HOLD'


def test_nonadmitting_authenticated_http_endpoint_synthetic(monkeypatch):
    monkeypatch.setattr(m,'check_node',lambda n:{'node':n,'expected_model_present':True,
                                                'advertised_unique_models':2})
    with m.PrivateServer(('127.0.0.1',0),m.Handler) as srv:
        port=srv.server_address[1]
        srv.allowed_client='127.0.0.1'
        srv.secret=KEY
        srv.seen=set()
        srv.requests_seen=0
        thread=threading.Thread(target=srv.serve_forever,daemon=True)
        thread.start()
        try:
            url=f'http://127.0.0.1:{port}/canary'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(sig_ok=True,nonce=None,body=NODE):
                timestamp = str(int(time.time()))
                nonce = nonce or os.urandom(16).hex()
                sig=m.sign(KEY,timestamp,nonce,body)
                if not sig_ok:
                    sig='0'*64
                req=urllib.request.Request(url,data=body,method='POST',headers={
                    'Content-Type':'application/json',
                    'X-Canary-Timestamp':timestamp,
                    'X-Canary-Nonce':nonce,
                    'X-Canary-Signature':sig})
                try:
                    with opener.open(req,timeout=2) as resp:
                        return resp.status,json.load(resp)
                except urllib.error.HTTPError as resp:
                    return resp.code,json.load(resp)
            assert post(sig_ok=False)[0]==403
            nonce='b'*32
            status,result=post(nonce=nonce)
            assert status==200
            assert result['task_admission']=='HOLD'
            assert result['approved_parallel_slots']==0
            assert result['production_ingress_authorized'] is False
            assert result['direct_lan_http_verified'] is False
            assert result['ssh_over_physical_lan_verified'] is True
            assert post(nonce=nonce)[0]==403
            assert post(body=b'{"node":"outsider"}')[0]==403
            get=urllib.request.Request(url,method='GET')
            with pytest.raises(urllib.error.HTTPError) as exc:
                opener.open(get,timeout=2)
            assert exc.value.code==405
        finally:
            srv.shutdown()
            thread.join(timeout=2)


def test_signed_request_from_unapproved_source_refused(monkeypatch):
    monkeypatch.setattr(m,'check_node',lambda _:pytest.fail('must deny before SSH'))
    with m.PrivateServer(('127.0.0.1',0),m.Handler) as srv:
        srv.allowed_client='172.21.0.6'
        srv.secret=KEY
        srv.seen=set()
        srv.requests_seen=0
        thread=threading.Thread(target=srv.serve_forever,daemon=True)
        thread.start()
        try:
            stamp=str(int(time.time()))
            nonce='c'*32
            req=urllib.request.Request(
                f'http://127.0.0.1:{srv.server_address[1]}/canary',
                data=NODE, method='POST', headers={
                    'Content-Type':'application/json',
                    'X-Canary-Timestamp':stamp,
                    'X-Canary-Nonce':nonce,
                    'X-Canary-Signature':m.sign(KEY,stamp,nonce,NODE)})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.HTTPError) as exc:
                opener.open(req,timeout=2)
            assert exc.value.code==403
            assert json.load(exc.value)['reason']=='source_not_allowed'
            assert not srv.seen
        finally:
            srv.shutdown()
            thread.join(timeout=2)


def test_no_task_routes_or_shared_credentials():
    source=(SRC/'fleet_signed_ssh_docker_catalog_canary.py').read_text()
    assert '/api/agent/tasks' not in source
    assert 'NEO4J_PASSWORD' not in source
    assert 'token_fd' not in source or 'os.read(secret_fd, 33)' in source
    assert 'approved_parallel_slots' in source and 'task_admission' in source
