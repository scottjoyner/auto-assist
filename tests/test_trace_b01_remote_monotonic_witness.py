"""Synthetic offline witness replay and recovery negatives; no graph access."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from hashlib import sha256
import shutil
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import trace_b01_remote_monotonic_witness as w
from trace_b01_native_claim_protocol import message

NOW=1855000000

def pub(key):
    return key.public_key().public_bytes_raw()

@pytest.fixture
def fx(tmp_path):
    issuer=Ed25519PrivateKey.generate()
    device=Ed25519PrivateKey.generate()
    witness=Ed25519PrivateKey.generate()
    db,anchor=tmp_path/'db'/'ledger.db',tmp_path/'anchor'/'checkpoint.json'
    db.parent.mkdir(); anchor.parent.mkdir()
    w.bootstrap(db,anchor,instance='a'*64,agent_id='fixture-agent-1234',
        policy_sha256='b'*64,issuer_root=pub(issuer),
        device_root=pub(device),witness_root=pub(witness))
    return w.FixtureRemoteWitness(db,anchor,pub(issuer),pub(device),witness),issuer,device

def signed(fx,*,nonce='nonce-testing123',sequence=1,term=1,
           issuer=None,device=None,agent='fixture-agent-1234',challenge='c'*64,
           issued=NOW-3,expires=NOW+35):
    guard,signer1,signer2=fx
    inputs=dict(instance='a'*64,task_id='fixture-task-1234',agent_id=agent,
        term=term,sequence=sequence,nonce=nonce,policy_sha256='b'*64,
        issued_at=issued,expires_at=expires)
    content=message(**inputs)
    return b'\n'.join((content,(issuer or signer1).sign(content).hex().encode(),
        (device or signer2).sign(content).hex().encode(),challenge.encode()))

def test_valid_reservation_is_durable_uncertain_and_receipt_signed(fx):
    guard,_,_=fx
    raw=signed(fx)
    receipt=guard.reserve(raw,now=NOW)
    assert receipt.startswith(b'PENDING\n')
    signature=bytes.fromhex(receipt.split(b'\n')[1].decode())
    guard.signer.public_key().verify(signature,w.RECEIPT_DOMAIN+sha256(raw).digest())
    assert guard.snapshot()['high_water']==1
    assert guard.snapshot()['pending']['term']==1
    reopened=w.FixtureRemoteWitness(guard.db,guard.anchor,guard.issuer_root,
                                     guard.device_root,guard.signer)
    with pytest.raises(w.WitnessDenied,match='UNCERTAIN_PENDING'):
        reopened.reserve(raw,now=NOW)

@pytest.mark.parametrize('changes',[{'issuer':Ed25519PrivateKey.generate()},
                                     {'device':Ed25519PrivateKey.generate()},
                                     {'agent':'fixture-agent-foreign'},
                                     {'sequence':2}, {'term':2},
                                     {'expires':NOW-1},
                                     {'issued':NOW+5}])
def test_forgery_foreign_stale_or_expired_denied(fx,changes):
    gate,*_=fx
    with pytest.raises(w.WitnessDenied):
        gate.reserve(signed(fx,**changes),now=NOW)
    assert gate.snapshot()['high_water']==0

@pytest.mark.parametrize('bad',[b'',b'raw',b'x'*2100,
       b'a\nb\nc', b'\xff'*20])
def test_unreviewed_requests_fail_closed(fx,bad):
    with pytest.raises(w.WitnessDenied):
        fx[0].reserve(bad,now=NOW)
    assert fx[0].snapshot()['high_water']==0

def test_same_sequence_concurrent_one_pending(fx):
    gate,*_=fx
    docs=[signed(fx,nonce='nonce-threadone123'),
          signed(fx,nonce='nonce-threadtwo123')]
    def submit(raw):
        try:
            return gate.reserve(raw,now=NOW)
        except w.WitnessDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,docs))
    assert sum(r is not None for r in results)==1
    assert gate.snapshot()['high_water']==1

def test_db_only_rollback_mismatch_detected(fx,tmp_path):
    gate,*_=fx
    before=tmp_path/'before.db'
    shutil.copy2(gate.db,before)
    gate.reserve(signed(fx),now=NOW)
    shutil.copy2(before,gate.db)
    with pytest.raises(w.WitnessDenied,match='ROLLBACK_OR_UNCERTAIN'):
        gate.snapshot()

def test_coordinated_local_db_and_anchor_restore_remains_no_go(fx,tmp_path):
    gate,*_=fx
    db=tmp_path/'db.bak'; anchor=tmp_path/'anchor.bak'
    shutil.copy2(gate.db,db);shutil.copy2(gate.anchor,anchor)
    original=signed(fx)
    gate.reserve(original,now=NOW)
    shutil.copy2(db,gate.db);shutil.copy2(anchor,gate.anchor)
    assert gate.snapshot()['high_water']==0
    assert gate.reserve(original,now=NOW).startswith(b'PENDING\n')

def test_witness_receipt_binds_fresh_challenge(fx):
    gate,*_=fx
    first=signed(fx,challenge='c'*64)
    second=signed(fx,challenge='d'*64)
    signature=bytes.fromhex(gate.reserve(first,now=NOW).split(b'\n')[1].decode())
    with pytest.raises(Exception):
        gate.signer.public_key().verify(signature,w.RECEIPT_DOMAIN+sha256(second).digest())

def test_source_has_no_auto_release_or_production_graph_routes():
    code=Path(__file__).with_name('trace_b01_remote_monotonic_witness.py').read_text()
    assert 'UNCERTAIN_PENDING_NO_REEXECUTE' in code
    assert 'BEGIN IMMEDIATE' in code
    assert "if self.path!='/reserve':" in code
    assert 'GraphDatabase.driver' not in code
    assert 'CALL b01research.' not in code
    assert 'os.getenv' not in code
    assert '_atomic_anchor(self.anchor,updated)' in code