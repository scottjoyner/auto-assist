"""Disposable, deny-by-default signed-only HTTP claim gateway. NOT production.

Only this isolated process has the fixture graph-writer credential. The
resource *service* validates signatures and reserves high-water before using
one fixed Cypher write. This is NOT Neo4j-native immutable authority: a
compromised gateway/credential can still bypass it with arbitrary Cypher.
"""
from __future__ import annotations
import json
import sys
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from neo4j import GraphDatabase
from trace_b01_signed_reservation_gate import (
    SignedResourceGate, SignedEnvelope, AdmissionDenied,
)
from trace_b01_disposable_claim_fence import FixtureClaim, try_claim

MAX_BODY = 4096
ROUTE = '/v1/fixture-claim'

class InvalidRequest(ValueError):
    pass


def strict_object(pairs):
    result={}
    for name,value in pairs:
        if name in result:
            raise InvalidRequest('DUPLICATE_JSON_FIELD')
        result[name]=value
    return result


def parse_fixed_envelope(payload):
    if type(payload) is not dict or set(payload) != {'envelope'}:
        raise InvalidRequest('UNREVIEWED_REQUEST_SHAPE')
    env = payload['envelope']
    if type(env) is not dict or set(env) != {
            'body', 'issuer_signature', 'device_signature'}:
        raise InvalidRequest('UNREVIEWED_ENVELOPE_SHAPE')
    if type(env['body']) is not dict:
        raise InvalidRequest('UNREVIEWED_SIGNED_BODY')
    parts=[]
    for key in ('issuer_signature','device_signature'):
        value=env[key]
        if (type(value) is not str or len(value)!=128
                or any(c not in '0123456789abcdef' for c in value)):
            raise InvalidRequest('UNREVIEWED_SIGNATURE_ENCODING')
        parts.append(bytes.fromhex(value))
    return SignedEnvelope(env['body'],parts[0],parts[1])


class SignedClaimHandler(BaseHTTPRequestHandler):
    server_version = 'SyntheticSignedClaimGateway/0'
    def log_message(self, *_args):
        # No credentials, payloads, signed bodies, raw errors, or graph data.
        pass

    def reply(self, code, response):
        payload=json.dumps(response,separators=(',',':')).encode('ascii')
        self.send_response(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200,{'status':'synthetic_only',
                                   'production_authority':False})
        return self.reply(404,{'error':'NOT_FOUND'})

    def do_POST(self):
        if self.path != ROUTE:
            return self.reply(404,{'error':'NOT_FOUND'})
        if not compare_digest(self.headers.get('Authorization',''),
                              'Bearer '+self.server.bearer):
            return self.reply(401,{'error':'UNAUTHORIZED'})
        try:
            length=int(self.headers.get('Content-Length','-1'))
        except ValueError:
            return self.reply(400,{'error':'INVALID_LENGTH'})
        if length < 1 or length > MAX_BODY:
            return self.reply(413,{'error':'INVALID_LENGTH'})
        try:
            raw=self.rfile.read(length)
            envelope=parse_fixed_envelope(
                json.loads(raw,object_pairs_hook=strict_object))
        except (ValueError,UnicodeError,RecursionError):
            return self.reply(422,{'error':'INVALID_FIXED_PLAN'})
        try:
            # Pinned roots and replay register live inside this fixture
            # gateway, not in a caller-provided header or verifier digest.
            pending=self.server.gate.reserve(envelope)
        except AdmissionDenied:
            return self.reply(403,{'error':'ADMISSION_DENIED'})
        # Claimed issuer+device signatures here are a *synthetic* stand-in
        # for recovery identity, not a production device attestation.
        try:
            body=envelope.body
            claim=FixtureClaim(task_id=body['task_id'],agent_id=body['agent_id'],
                term=body['term'],sequence=body['sequence'],nonce=body['nonce'],
                authority_digest=pending['proof_sha256'],
                recovery_identity_verified=True)
        except (KeyError,TypeError,ValueError):
            return self.reply(503,{'error':'UNCERTAIN_GRAPH_EFFECT'})
        try:
            result=try_claim(self.server.driver,claim)
        except Exception:
            # Ledger stays PENDING_UNCERTAIN. No automatic replay/cleanup.
            return self.reply(503,{'error':'UNCERTAIN_GRAPH_EFFECT'})
        if result is None:
            return self.reply(503,{'error':'UNCERTAIN_GRAPH_EFFECT'})
        return self.reply(200,{
            'state':'PENDING_UNCERTAIN','synthetic_graph_effect_applied':True,
            'production_authority':False,'release_authorized':False,
            'automatic_reexecution_allowed':False})


def main():
    if len(sys.argv)!=2 or not sys.argv[1].startswith('/tmp/'):
        raise RuntimeError('DISPOSABLE_CONFIG_REQUIRED')
    config=json.loads(Path(sys.argv[1]).read_text())
    if set(config)!={'bearer','writer_user','writer_password',
                    'issuer_root','device_root','task_id','agent_id',
                    'policy_sha256'}:
        raise RuntimeError('INVALID_FIXTURE_CONFIG')
    roots=(bytes.fromhex(config['issuer_root']),
           bytes.fromhex(config['device_root']))
    # The test harness provisions a fresh, root-pinned fixture ledger before
    # launch. Missing/changed ledger or anchor must DENY, never rebootstrap.
    gate=SignedResourceGate(
        Path('/tmp/signed-gate-db/ledger.sqlite'),
        Path('/tmp/signed-gate-anchor/checkpoint.json'),*roots)
    state=gate.snapshot()
    if (state['task_id'] != config['task_id'] or
            state['agent_id'] != config['agent_id'] or
            state['policy_sha256'] != config['policy_sha256']):
        raise RuntimeError('DISPOSABLE_GATE_BINDING_MISMATCH')
    driver=GraphDatabase.driver('bolt://neo4j:7687',
        auth=(config['writer_user'],config['writer_password']),
        connection_timeout=3,connection_acquisition_timeout=4)
    driver.verify_connectivity()
    server=ThreadingHTTPServer(('0.0.0.0',8809),SignedClaimHandler)
    server.bearer=config['bearer']
    server.gate=gate
    server.driver=driver
    try:
        server.serve_forever(poll_interval=0.15)
    finally:
        server.server_close()
        driver.close()

if __name__ == '__main__':
    main()