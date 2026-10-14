"""Research-only fail-closed witness/native-procedure source invariants."""
from pathlib import Path
import pytest
from trace_b01_remote_monotonic_witness import parse, WitnessDenied
from trace_b01_native_claim_protocol import message

ROOT=Path(__file__).resolve().parent

BASE=dict(instance='a'*64,task_id='fixture-task-1234',
    agent_id='fixture-agent-5678',term=1,sequence=1,
    nonce='nonce-testing123',policy_sha256='b'*64,
    issued_at=1855000000,expires_at=1855000040)

def raw(**edits):
    fields=BASE|edits
    return message(**fields)+b'\n'+b'a'*128+b'\n'+b'b'*128+b'\n'+b'c'*64

def test_native_receipt_request_has_exact_fixed_native_message():
    fields,issuer,device=parse(raw())
    assert fields==BASE
    assert issuer==bytes.fromhex('a'*128)
    assert device==bytes.fromhex('b'*128)

@pytest.mark.parametrize('bad',[b'',b'a'*2500,
    raw()+b'\nextra',raw()[:-1]+b'Z',
    raw().replace(b'B01-NATIVE-CLAIM-v0',b'B01-FORGED-CLAIM-v0'),
    raw().replace(b'nonce-testing123',b'nonce-contains space'),
    raw().replace(b'\n1\n1\nnonce',b'\n01\n1\nnonce'),
])
def test_noncanonical_signed_witness_requests_denied(bad):
    with pytest.raises(WitnessDenied):
        parse(bad)

def test_native_verifies_pinned_three_roots_and_challenge_bound_receipt():
    java=(ROOT/'native_b01'/'FixtureSignedClaim.java').read_text()
    for name in ('B01_FIXTURE_ISSUER_ROOT','B01_FIXTURE_DEVICE_ROOT',
                 'B01_FIXTURE_WITNESS_ROOT'):
        assert f'envRoot("{name}")' in java
    assert 'B01-WITNESS-PENDING-v0\\n' in java
    assert 'SecureRandom().nextBytes(challenge)' in java
    assert 'verify(envRoot("B01_FIXTURE_WITNESS_ROOT")' in java
    assert java.index('verify(device,deviceSig,bytes)') < java.index('tx.findNode(')
    assert java.index('requireWitness(issuerSig,deviceSig,bytes)') < java.index('guard.setProperty("high_water",sequence)')
    assert 'Proxy.NO_PROXY' in java
    assert 'link.setConnectTimeout(2500)' in java
    assert 'link.setReadTimeout(4000)' in java
    assert 'link.setInstanceFollowRedirects(false)' in java
    assert 'throw denied();' in java
    assert 'B01_FIXTURE_CLASS' not in java


def test_native_resource_does_not_delegate_policy_to_caller_chosen_endpoint():
    src=(ROOT/'native_b01'/'FixtureSignedClaim.java').read_text()
    assert '"http://b01-witness:8812/reserve".equals(url)' in src
    assert '@Procedure(name="b01research.claimFixture",mode=Mode.WRITE)' in src
    assert 'tx.acquireWriteLock(guard)' in src
    assert 'guard.setProperty("high_water",sequence)' in src
    assert 'task.setProperty("status","CLAIMED")' in src
    assert 'SYNTHETIC_ADMISSION_DENIED' in src
    assert 'GraphDatabase.driver' not in src


def test_witness_stores_pending_before_receipt_and_cannot_auto_replay():
    src=(ROOT/'trace_b01_remote_monotonic_witness.py').read_text()
    assert src.index('_atomic_anchor(self.anchor,updated)') < src.index('return b\'PENDING\\n\'')
    assert 'UNCERTAIN_PENDING_NO_REEXECUTE' in src
    assert 'BEGIN IMMEDIATE' in src
    assert 'RECEIPT_DOMAIN+sha256(raw).digest()' in src
    assert 'FORGED_WITNESS_CLAIM' in src
    assert 'WITNESS_ROLLBACK_OR_UNCERTAIN' in src
    assert "self.path!='/reserve'" in src
    for bad in ('GraphDatabase.driver','neo4j://','docker network disconnect',
                'automatic_reexecute','release_pending'):
        assert bad not in src


def test_disposable_runner_has_three_independent_cases_and_no_host_ports():
    code=(ROOT/'probe_trace_b01_remote_witness_physical.py').read_text()
    for expected in (
        "ASSISTX_B01_REMOTE_WITNESS_PHYSICAL",
        "if mode not in ('normal','outage','co_restore')",
        "'--internal','--driver','bridge'",
        "assert_disposable(witness,{witness_net})",
        "assert_disposable(graph,{net,witness_net})",
        "'--network','connect'",  # normalized only if present
    ):
        if expected=="'--network','connect'":
            assert "docker('network','connect',witness_net,graph)" in code
        else:
            assert expected in code
    assert 'PASS_GRAPH_ONLY_REPLAY_DENIED_PENDING_NO_GO' in code
    assert 'PASS_FAIL_CLOSED_UNCERTAIN_NO_AUTO_RETRY' in code
    assert 'PASS_EXPECTED_COHERENT_ROLLBACK_REPLAY_NO_GO' in code
    assert "docker('rm','-f','-v',graph" in code
    assert "docker('rm','-f','-v',witness" in code
    assert "docker('network','rm',witness_net" in code
    assert "docker('network','rm',net" in code
    assert 'production_admission_authorized' in code
    for unsafe in ('docker network disconnect','docker exec assistx-api',
                   'NEO4J_PASSWORD','Tailscale','/NAS5/'):
        assert unsafe not in code