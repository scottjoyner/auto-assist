"""Disposable unprivileged HTTP actor; no Neo4j client or writer credential."""
from __future__ import annotations
import json
from pathlib import Path
import socket
import sys
import urllib.request
import urllib.error

CFG=json.loads(Path('/tmp/actor.json').read_text())
BASE='http://'+CFG['gateway']+':8809'

def tcp_open(host,port):
    try:
        with socket.create_connection((host,port),timeout=1.0):
            return True
    except OSError:
        return False

def http_request(path,body=None,*,bearer=True):
    headers={'Content-Type':'application/json'}
    if bearer:
        headers['Authorization']='Bearer '+CFG['bearer']
    request=urllib.request.Request(BASE+path,
        data=json.dumps(body).encode('utf-8') if body is not None else None,
        method='POST' if body is not None else 'GET',headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=6) as response:
            return response.status,json.loads(response.read(4096))
    except urllib.error.HTTPError as exc:
        return exc.code,json.loads(exc.read(4096))

def run(phase):
    assert not tcp_open(CFG['graph_ip'],7687), 'DIRECT_BOLT_IP_REACHABLE'
    assert not tcp_open(CFG['graph_ip'],7474), 'DIRECT_GRAPH_HTTP_IP_REACHABLE'
    assert not tcp_open('neo4j',7687), 'DIRECT_BOLT_DNS_REACHABLE'
    assert not tcp_open('neo4j',7474), 'DIRECT_GRAPH_HTTP_DNS_REACHABLE'
    assert tcp_open(CFG['gateway'],8809), 'GATEWAY_UNREACHABLE'
    assert http_request('/health')==(200,{'status':'synthetic_only',
                                         'production_authority':False})
    valid={'envelope':CFG['signed']}
    if phase=='initial':
        assert http_request('/v1/fixture-claim',valid,bearer=False)==(
            401,{'error':'UNAUTHORIZED'})
        assert http_request('/v1/extra',valid)==(
            404,{'error':'NOT_FOUND'})
        assert http_request('/v1/fixture-claim',{
            **valid,'cypher':'MATCH (n) DETACH DELETE n'})==(
            422,{'error':'INVALID_FIXED_PLAN'})
        assert http_request('/v1/fixture-claim',{
            'plan_id':'claim_fixture_task','digest':'a'*64})==(
            422,{'error':'INVALID_FIXED_PLAN'})
        assert http_request('/v1/fixture-claim',{
            'envelope':{**CFG['signed'],
                        'issuer_signature':CFG['forged_issuer_signature']}})==(
            403,{'error':'ADMISSION_DENIED'})
        assert http_request('/v1/fixture-claim',{
            'envelope':{**CFG['signed'],
                        'device_signature':CFG['forged_device_signature']}})==(
            403,{'error':'ADMISSION_DENIED'})
        assert http_request('/v1/fixture-claim',{
            'envelope':{**CFG['signed'],
                        'body':{**CFG['signed']['body'],
                                'agent_id':'fixture-agent-malicious123'}}})==(
            403,{'error':'ADMISSION_DENIED'})
        code,result=http_request('/v1/fixture-claim',valid)
        assert code==200,(code,result)
        assert result=={
            'state':'PENDING_UNCERTAIN','synthetic_graph_effect_applied':True,
            'production_authority':False,'release_authorized':False,
            'automatic_reexecution_allowed':False}
        assert http_request('/v1/fixture-claim',valid)==(
            403,{'error':'ADMISSION_DENIED'})
        return dict(phase='initial',valid_signed_claim=True,
            compromised_bearer_forgery_denied=True,
            raw_cypher_and_digest_denied=True,
            signature_and_identity_tamper_denied=True,
            immediate_replay_denied=True,graph_network_denied=True)
    if phase=='after_graph_restore':
        assert http_request('/v1/fixture-claim',valid)==(
            403,{'error':'ADMISSION_DENIED'})
        return dict(phase='after_graph_restore',
                    restored_graph_replay_denied_by_ledger=True,
                    graph_network_denied=True)
    raise RuntimeError('UNREVIEWED_FIXTURE_PHASE')

if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit(2)
    print(json.dumps(run(sys.argv[1]),sort_keys=True))