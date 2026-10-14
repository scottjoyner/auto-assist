"""Offline exact synthetic HTTP gateway boundary regression."""
import json
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
import urllib.request
import urllib.error
from unittest.mock import Mock,patch
import pytest
import trace_b01_signed_claim_http_gateway as gw

GOOD={'envelope':{'body':{'schema':'fixture'},
      'issuer_signature':'a'*128,'device_signature':'b'*128}}

def test_exact_fixed_envelope_parses_both_signatures():
    result=gw.parse_fixed_envelope(GOOD)
    assert result.issuer_signature==bytes.fromhex('a'*128)
    assert result.device_signature==bytes.fromhex('b'*128)
    assert result.body=={'schema':'fixture'}

@pytest.mark.parametrize('payload',[
    {},{'envelope':{}},{'envelope':[]},
    dict(GOOD,cypher='MATCH (n) RETURN n'),
    {'envelope':{**GOOD['envelope'],'signer_root':'caller-key'}},
    {'envelope':{**GOOD['envelope'],'device_signature':'bad'}},
    {'envelope':{**GOOD['envelope'],'device_signature':42}},
    {'envelope':{**GOOD['envelope'],'body':'not a dict'}},
    {'envelope':{**GOOD['envelope'],'issuer_signature':'g'*128}},
])
def test_rejects_arbitrary_metadata_queries_and_invalid_signatures(payload):
    with pytest.raises(gw.InvalidRequest):
        gw.parse_fixed_envelope(payload)

@pytest.mark.parametrize('raw',[
    '{"envelope":{},"envelope":{}}',
    '{"envelope":{"body":{"id":1,"id":2}}}',
])
def test_rejects_nested_and_top_level_duplicate_json_keys(raw):
    with pytest.raises(gw.InvalidRequest,match='DUPLICATE_JSON_FIELD'):
        json.loads(raw,object_pairs_hook=gw.strict_object)

def send(port,payload,*,token='fixture-bearer'):
    req=urllib.request.Request('http://127.0.0.1:'+str(port)+gw.ROUTE,
        method='POST',data=json.dumps(payload).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=4) as out:
            return out.status,json.loads(out.read(1024))
    except urllib.error.HTTPError as exc:
        return exc.code,json.loads(exc.read(1024))

def test_authenticated_gate_denies_uncertain_graph_effect_without_regrant():
    gate=Mock()
    gate.reserve.return_value={'proof_sha256':'a'*64}
    server=ThreadingHTTPServer(('127.0.0.1',0),gw.SignedClaimHandler)
    server.bearer='fixture-bearer'
    server.gate=gate
    server.driver=object()
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with patch.object(gw,'try_claim',side_effect=OSError('synthetic disk error')) as effect:
            assert send(server.server_port,GOOD,token='wrong')==(
                401,{'error':'UNAUTHORIZED'})
            assert gate.reserve.call_count==0
            # Even a broken fixture gate returning a permit for an incomplete
            # body must not leak a stack trace or call the graph writer.
            assert send(server.server_port,GOOD)==(
                503,{'error':'UNCERTAIN_GRAPH_EFFECT'})
            assert gate.reserve.call_count==1
            assert effect.call_count==0
            full={'envelope':{**GOOD['envelope'],
                'body':{'task_id':'fixture-task-1234',
                        'agent_id':'fixture-agent-1234',
                        'term':1,'sequence':1,'nonce':'nonce-123456'}}}
            assert send(server.server_port,full)==(
                503,{'error':'UNCERTAIN_GRAPH_EFFECT'})
            assert gate.reserve.call_count==2
            assert effect.call_count==1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

def test_fixture_gateway_has_no_production_routes_and_no_auto_release():
    src=Path(__file__).with_name('trace_b01_signed_claim_http_gateway.py').read_text()
    assert 'self.server.gate.reserve(envelope)' in src
    assert src.index('self.server.gate.reserve(envelope)') < src.index('try_claim(')
    assert "state':'PENDING_UNCERTAIN'" in src
    assert "'production_authority':False" in src
    assert "'automatic_reexecution_allowed':False" in src
    assert 'GraphDatabase.driver' in src
    assert 'CALL dbms' not in src
    assert 'cypher' not in src.split('def parse_fixed_envelope')[1].split('class SignedClaimHandler')[0].lower()

def test_disposable_network_roles_are_separated_in_source():
    src=Path(__file__).with_name('probe_trace_b01_signed_claim_http_physical.py').read_text()
    assert 'assert_disposable(gateway,{worker_net,graph_net})' in src
    assert 'assert_disposable(actor,{worker_net})' in src
    assert 'assert_disposable(graph,{graph_net})' in src
    assert 'EXPLICIT_DISPOSABLE_SIGNED_HTTP_OPT_IN_REQUIRED' in src
    assert 'host_published_graph_ports=False' in src
    assert 'compromised_graph_writer_bypass_reproduced=True' in src
    assert 'docker("rm","-f","-v",name' in src
    assert 'docker("network","rm",name' in src