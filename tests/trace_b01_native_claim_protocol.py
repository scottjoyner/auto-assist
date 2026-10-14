"""Canonical lab-only message binding for Neo4j-native Ed25519 claim procedure."""
from __future__ import annotations
import re

_ID = re.compile(r'fixture-(?:task|agent)-[A-Za-z0-9_-]{4,48}')
_NONCE = re.compile(r'nonce-[A-Za-z0-9_-]{6,48}')
_HEX = re.compile(r'[a-f0-9]{64}')


def message(*, instance: str, task_id: str, agent_id: str,
            term: int, sequence: int, nonce: str,
            policy_sha256: str, issued_at: int, expires_at: int) -> bytes:
    if (type(instance) is not str or _HEX.fullmatch(instance) is None
            or type(task_id) is not str or _ID.fullmatch(task_id) is None
            or not task_id.startswith('fixture-task-')
            or type(agent_id) is not str or _ID.fullmatch(agent_id) is None
            or not agent_id.startswith('fixture-agent-')
            or type(nonce) is not str or _NONCE.fullmatch(nonce) is None
            or type(policy_sha256) is not str or _HEX.fullmatch(policy_sha256) is None
            or any(type(x) is not int for x in (term, sequence, issued_at, expires_at))
            or term < 1 or sequence < 1
            or expires_at <= issued_at or expires_at-issued_at > 60):
        raise ValueError('UNREVIEWED_SYNTHETIC_NATIVE_EVIDENCE')
    items=('B01-NATIVE-CLAIM-v0',instance,'fixture-assistx-b01',
        'claim_fixture_task',task_id,agent_id,str(term),str(sequence),
        nonce,policy_sha256,str(issued_at),str(expires_at))
    return '\n'.join(items).encode('ascii')


def params(*, instance: str, task_id: str, agent_id: str,
            term: int, sequence: int, nonce: str,
            policy_sha256: str, issued_at: int, expires_at: int,
            issuer, device) -> dict:
    inputs=dict(instance=instance,task_id=task_id,agent_id=agent_id,
                term=term,sequence=sequence,nonce=nonce,
                policy_sha256=policy_sha256,issued_at=issued_at,
                expires_at=expires_at)
    content=message(**inputs)
    return dict(**inputs,issuer_sig_hex=issuer.sign(content).hex(),
                device_sig_hex=device.sign(content).hex())


CALL = ('CALL b01research.claimFixture($instance, $task_id, $agent_id, '
        '$term, $sequence, $nonce, $policy_sha256, $issued_at, $expires_at, '
        '$issuer_sig_hex, $device_sig_hex) '
        'YIELD result, term, highWater '
        'RETURN result, term, highWater')