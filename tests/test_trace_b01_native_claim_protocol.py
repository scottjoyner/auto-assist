"""Pure source and canonical protocol tests for Neo4j-native fixture claim."""
from pathlib import Path
from dataclasses import replace
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from trace_b01_native_claim_protocol import message,params,CALL

BASE=dict(instance='a'*64,task_id='fixture-task-1234',
    agent_id='fixture-agent-5678',term=1,sequence=1,
    nonce='nonce-testing123',policy_sha256='b'*64,
    issued_at=1855000000,expires_at=1855000050)

def test_fixture_message_bytes_are_frozen_and_distinctly_signed():
    content=message(**BASE)
    assert content==('\n'.join(('B01-NATIVE-CLAIM-v0','a'*64,
        'fixture-assistx-b01','claim_fixture_task','fixture-task-1234',
        'fixture-agent-5678','1','1','nonce-testing123','b'*64,
        '1855000000','1855000050'))).encode()
    signer1,signer2=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    result=params(**BASE,issuer=signer1,device=signer2)
    signer1.public_key().verify(bytes.fromhex(result['issuer_sig_hex']),content)
    signer2.public_key().verify(bytes.fromhex(result['device_sig_hex']),content)
    assert result['issuer_sig_hex']!=result['device_sig_hex']

@pytest.mark.parametrize('changes',[
    {'task_id':'other-task'},{'agent_id':'fixture-agent-bad\nactor'},
    {'nonce':'bad'}, {'term':False},{'sequence':0}, {'instance':'z'*64},
    {'policy_sha256':'non-policy'}, {'issued_at':1855000100},
    {'expires_at':1855000900},
])
def test_bad_or_expired_fixture_message_shapes_denied(changes):
    with pytest.raises(ValueError):
        message(**(BASE|changes))

def test_native_java_declares_two_pinned_signature_checks_before_graph_access():
    code=Path(__file__).with_name('native_b01').joinpath('FixtureSignedClaim.java').read_text()
    assert 'KeyFactory.getInstance("Ed25519")' in code
    assert 'Signature.getInstance("Ed25519")' in code
    assert code.index('verify(issuer,issuerSig,bytes)') < code.index('tx.findNode(')
    assert code.index('verify(device,deviceSig,bytes)') < code.index('tx.findNode(')
    assert 'tx.acquireWriteLock(guard)' in code
    assert 'high_water' in code
    assert '@Procedure(name="b01research.claimFixture",mode=Mode.WRITE)' in code
    assert 'FIXTURE' in code
    assert 'GraphDatabase.driver' not in code
    assert 'allowlist' not in code
    assert 'getProperty("instance"' in code
    assert 'SYNTHETIC_ADMISSION_DENIED' in code
    assert 'SET t.status' not in CALL


def test_disposable_fixture_native_procedure_never_installs_prod_plugin():
    root=Path(__file__).with_name('probe_trace_b01_neo4j_native_ed25519_physical.py').read_text()
    assert 'EXPLICIT_DISPOSABLE_NATIVE_OPT_IN_REQUIRED' in root
    assert 'ASSISTX_B01_NATIVE_CRYPTO_PHYSICAL' in root
    assert "'--internal','--driver','bridge'" in root
    assert 'assert_disposable(graph,{net})' in root
    assert "'--network','none'" in root  # JDK compilation cannot use egress
    assert "'--entrypoint','/opt/java/openjdk/bin/javac'" in root
    assert 'production_neo4j_plugin_installed' in root
    assert 'valid_signed_proof_reaccepted_after_graph_rewind' in root
    assert "docker('rm','-f','-v',graph" in root
    assert "docker('network','rm',net" in root
    for marker in ('assistx-api','assistx-worker','NEO4J_PASSWORD',
                   'docker network disconnect','docker restart',
                   '/NAS5/', 'tailscale', 'NEO4J_uri'):
        assert marker not in root


def test_java_native_call_order_and_scope_are_fixed():
    source=Path(__file__).with_name('native_b01').joinpath('FixtureSignedClaim.java').read_text()
    assert 'fixture-assistx-b01' in source
    assert 'claim_fixture_task' in source
    assert 'B01-NATIVE-CLAIM-v0' in source
    assert 'B01_FIXTURE_ISSUER_ROOT' in source
    assert 'B01_FIXTURE_DEVICE_ROOT' in source
    assert 'tx.acquireWriteLock(guard)' in source
    assert 'guard.setProperty("high_water",sequence)' in source
    assert 'task.setProperty("status","CLAIMED")' in source
    assert source.index('verify(device,deviceSig,bytes)') < source.index('tx.findNode(')
    assert 'System.getenv("NEO4J_PASSWORD")' not in source