"""Disposable NON-PRODUCTION signature-verified monotonic reservation service.

Keeps fixture replay custody outside Neo4j's restore domain. The resource's
native procedure must receive a fresh-challenge-bound Ed25519 receipt before
graph mutation. Pending is never released, recovered, reissued or promoted.
This does not survive coordinated witness-DB+anchor rollback, admin compromise,
external writer bypass, malicious extension, or a lost witness container.
"""
from __future__ import annotations
from contextlib import closing
from hashlib import sha256
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import re
import sys
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey, Ed25519PrivateKey)
from trace_b01_native_claim_protocol import message
from trace_b01_signed_reservation_gate import (
    _connect, _atomic_anchor, _read_anchor, AdmissionDenied)

SCHEMA='assistx-b01-remote-witness-v0'
RECEIPT_DOMAIN=b'B01-WITNESS-PENDING-v0\n'
RANDOM_HEX=re.compile(r'[a-f0-9]{64}')
HEX_SIG=re.compile(r'[a-f0-9]{128}')

class WitnessDenied(ValueError):
    pass


def bootstrap(db: Path, anchor: Path, *, instance: str,
              agent_id: str, policy_sha256: str,
              issuer_root: bytes, device_root: bytes, witness_root: bytes):
    db,anchor=Path(db),Path(anchor)
    if db.exists() or anchor.exists() or db.parent.resolve()==anchor.parent.resolve():
        raise WitnessDenied('UNSAFE_WITNESS_BOOTSTRAP')
    if (not RANDOM_HEX.fullmatch(instance) or
            not RANDOM_HEX.fullmatch(policy_sha256) or
            not isinstance(agent_id,str) or not agent_id.startswith('fixture-agent-')
            or any(type(k) is not bytes or len(k)!=32 for k in (
                issuer_root,device_root,witness_root)) or issuer_root==device_root):
        raise WitnessDenied('INVALID_PINNED_WITNESS')
    state=dict(schema=SCHEMA,instance=instance,agent_id=agent_id,
        policy_sha256=policy_sha256,term=1,high_water=0,
        used_nonces=[],pending=None,issuer_root=issuer_root.hex(),
        device_root=device_root.hex(),witness_root=witness_root.hex())
    with closing(_connect(db)) as conn, conn:
        conn.execute('CREATE TABLE state(id INTEGER PRIMARY KEY, document TEXT NOT NULL)')
        conn.execute('INSERT INTO state VALUES(1,?)',(
            json.dumps(state,sort_keys=True,separators=(',',':')),))
    _atomic_anchor(anchor,state)


def parse(raw: bytes):
    if type(raw) is not bytes or len(raw)>2048 or not raw.isascii():
        raise WitnessDenied('NONCANONICAL_WITNESS_REQUEST')
    parts=raw.decode('ascii').split('\n')
    if len(parts)!=15:
        raise WitnessDenied('FIXED_WITNESS_PROOF_REQUIRED')
    domain,inst,scope,op,task,agent,t,seq,nonce,policy,issued,expiry,si,sd,challenge=parts
    if (domain!='B01-NATIVE-CLAIM-v0' or scope!='fixture-assistx-b01'
            or op!='claim_fixture_task' or
            not RANDOM_HEX.fullmatch(challenge) or
            not HEX_SIG.fullmatch(si) or not HEX_SIG.fullmatch(sd)):
        raise WitnessDenied('UNREVIEWED_WITNESS_PROOF_FIELDS')
    try:
        numerical=[int(x) for x in (t,seq,issued,expiry)]
        fields=dict(instance=inst,task_id=task,agent_id=agent,term=numerical[0],
            sequence=numerical[1],nonce=nonce,policy_sha256=policy,
            issued_at=numerical[2],expires_at=numerical[3])
        exact=message(**fields)
    except (ValueError,TypeError):
        raise WitnessDenied('INVALID_NATIVE_PROOF') from None
    if exact!=b'\n'.join(raw.split(b'\n')[:12]):
        raise WitnessDenied('NONCANONICAL_FIXED_NATIVE_PROOF')
    return fields, bytes.fromhex(si), bytes.fromhex(sd)


class FixtureRemoteWitness:
    def __init__(self, db, anchor, issuer_root, device_root, witness_signer):
        self.db,self.anchor=Path(db),Path(anchor)
        self.issuer_root=issuer_root
        self.device_root=device_root
        self.signer=witness_signer
        self.snapshot()

    def _state(self,conn):
        try:
            row=conn.execute('SELECT document FROM state WHERE id=1').fetchone()
            if row is None:
                raise WitnessDenied('WITNESS_LEDGER_MISSING')
            state=json.loads(row[0])
            anchor=_read_anchor(self.anchor)
        except (OSError,ValueError,TypeError,AdmissionDenied):
            raise WitnessDenied('WITNESS_LEDGER_OR_ANCHOR_UNAVAILABLE') from None
        if state!=anchor:
            raise WitnessDenied('WITNESS_ROLLBACK_OR_UNCERTAIN')
        if (state.get('schema')!=SCHEMA or
                state.get('issuer_root')!=self.issuer_root.hex() or
                state.get('device_root')!=self.device_root.hex() or
                state.get('witness_root')!=self.signer.public_key().public_bytes_raw().hex()):
            raise WitnessDenied('WITNESS_ROOT_OR_SCHEMA_MISMATCH')
        return state

    def snapshot(self):
        with closing(_connect(self.db)) as conn:
            return self._state(conn)

    def reserve(self, raw: bytes, *,now: int|None=None):
        fields, sig_issuer, sig_device=parse(raw)
        instant=int(time.time()) if now is None else now
        if not fields['issued_at']<=instant<fields['expires_at']:
            raise WitnessDenied('EXPIRED_OR_FUTURE_PROOF')
        content=message(**fields)
        try:
            Ed25519PublicKey.from_public_bytes(self.issuer_root).verify(sig_issuer,content)
            Ed25519PublicKey.from_public_bytes(self.device_root).verify(sig_device,content)
        except (InvalidSignature,ValueError):
            raise WitnessDenied('FORGED_WITNESS_CLAIM') from None
        with closing(_connect(self.db)) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            state=self._state(conn)
            if (fields['instance']!=state['instance'] or
                    fields['agent_id']!=state['agent_id'] or
                    fields['policy_sha256']!=state['policy_sha256']):
                raise WitnessDenied('UNAUTHORIZED_PROVENANCE')
            if state['pending'] is not None:
                raise WitnessDenied('UNCERTAIN_PENDING_NO_REEXECUTE')
            if (fields['term']!=state['term'] or
                    fields['sequence']!=state['high_water']+1 or
                    fields['nonce'] in state['used_nonces']):
                raise WitnessDenied('STALE_FORKED_OR_REPLAYED')
            updated={**state,'high_water':fields['sequence'],
                'used_nonces':[*state['used_nonces'],fields['nonce']],
                'pending':{'request_sha256':sha256(raw).hexdigest(),
                           'term':fields['term'],'sequence':fields['sequence'],
                           'task_id':fields['task_id'],
                           'agent_id':fields['agent_id'],
                           'nonce':fields['nonce']}}
            _atomic_anchor(self.anchor,updated)  # crash first => mismatch/NO-GO
            conn.execute('UPDATE state SET document=? WHERE id=1',(
                json.dumps(updated,sort_keys=True,separators=(',',':')),))
        # Receipt binds the full new request, including a Java-generated
        # random challenge; a recorded earlier reply cannot be replayed.
        signature=self.signer.sign(RECEIPT_DOMAIN+sha256(raw).digest())
        return b'PENDING\n'+signature.hex().encode('ascii')


class Handler(BaseHTTPRequestHandler):
    server_version='B01SyntheticWitness/0'
    def log_message(self,*args):
        pass
    def respond(self,code,body):
        self.send_response(code)
        self.send_header('Content-Type','text/plain')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path=='/health':
            return self.respond(200,b'FIXTURE_WITNESS_ONLINE')
        self.respond(404,b'NOT_FOUND')
    def do_POST(self):
        if self.path!='/reserve':
            return self.respond(404,b'NOT_FOUND')
        if not compare_digest(self.headers.get('X-Fixture-Graph-Bearer',''),
                              self.server.bearer):
            return self.respond(403,b'DENIED')
        try:
            length=int(self.headers.get('Content-Length','-1'))
            if length<1 or length>2048:
                raise WitnessDenied('INVALID_REQUEST_LENGTH')
            result=self.server.witness.reserve(self.rfile.read(length))
        except (WitnessDenied,ValueError,TypeError,OSError):
            return self.respond(403,b'DENIED')
        return self.respond(200,result)


def main():
    if len(sys.argv)!=2 or not sys.argv[1].startswith('/tmp/'):
        raise RuntimeError('DISPOSABLE_WITNESS_CONFIG_REQUIRED')
    data=json.loads(Path(sys.argv[1]).read_text())
    if set(data)!={'db','anchor','bearer','issuer_root','device_root','witness_seed'}:
        raise RuntimeError('UNREVIEWED_WITNESS_CONFIGURATION')
    witness=FixtureRemoteWitness(data['db'],data['anchor'],
        bytes.fromhex(data['issuer_root']), bytes.fromhex(data['device_root']),
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data['witness_seed'])))
    server=ThreadingHTTPServer(('0.0.0.0',8812),Handler)
    server.bearer=data['bearer']
    server.witness=witness
    try:
        server.serve_forever(poll_interval=0.15)
    finally:
        server.server_close()

if __name__=='__main__':
    main()