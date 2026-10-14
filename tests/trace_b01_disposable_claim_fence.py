"""Synthetic fixture resource-side claim CAS; NEVER imported by production.

One fixed Neo4j Cypher statement serializes on a fixture Fence node in an
explicit transaction. A denied match rolls back the temporary lock write.
This proves fixed-statement behavior, NOT cryptographic admission authority:
any principal allowed arbitrary graph mutations may bypass the statement,
and coordinated graph/checkpoint rollback is not prevented.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

SCOPE = "fixture-assistx-b01"
TASK_ID_RE = re.compile(r"fixture-task-[A-Za-z0-9_-]{4,48}")
AGENT_RE = re.compile(r"fixture-agent-[A-Za-z0-9_-]{4,48}")
NONCE_RE = re.compile(r"nonce-[A-Za-z0-9_-]{6,48}")
DIGEST_RE = re.compile(r"[a-f0-9]{64}")

class FencedDenied(ValueError):
    pass
@dataclass(frozen=True)
class FixtureClaim:
    task_id: str
    agent_id: str
    term: int
    sequence: int
    nonce: str
    authority_digest: str
    recovery_identity_verified: bool

def validate(claim: FixtureClaim) -> dict:
    if type(claim) is not FixtureClaim:
        raise FencedDenied("FIXTURE_CLAIM_REQUIRED")
    if (type(claim.task_id) is not str or
            TASK_ID_RE.fullmatch(claim.task_id) is None or
            type(claim.agent_id) is not str or
            AGENT_RE.fullmatch(claim.agent_id) is None or
            type(claim.nonce) is not str or
            NONCE_RE.fullmatch(claim.nonce) is None or
            type(claim.authority_digest) is not str or
            DIGEST_RE.fullmatch(claim.authority_digest) is None):
        raise FencedDenied("INVALID_FIXTURE_IDENTIFIER")
    if (type(claim.term) is not int or claim.term < 1
            or type(claim.sequence) is not int or claim.sequence < 1
            or type(claim.recovery_identity_verified) is not bool):
        raise FencedDenied("INVALID_FIXTURE_AUTHORITY_FIELDS")
    return dict(scope=SCOPE, task_id=claim.task_id, agent_id=claim.agent_id,
                term=claim.term, sequence=claim.sequence, nonce=claim.nonce,
                authority_digest=claim.authority_digest,
                recovery_identity_verified=claim.recovery_identity_verified)
# The temporary mutex increment takes a write lock on the *single* scope guard
# before evaluating term/high-water. Unsuccessful attempts MUST roll back their
# entire transaction, including the mutex. No cross-transaction read-before-CAS.
CYPHER = """
MATCH (g:FixtureFence {scope:$scope})
SET g.mutex = coalesce(g.mutex,0) + 1
WITH g
MATCH (t:FixtureTask {id:$task_id})
WHERE g.term = $term
  AND g.high_water + 1 = $sequence
  AND g.authority_digest = $authority_digest
  AND NOT $nonce IN g.seen_nonces
  AND t.status = 'READY'
  AND t.owner_id = $agent_id
  AND (coalesce(t.requires_recovery_identity,false) = false
       OR $recovery_identity_verified = true)
SET g.high_water = $sequence,
    g.seen_nonces = g.seen_nonces + [$nonce],
    t.status = 'CLAIMED',
    t.claimed_by = $agent_id,
    t.claim_nonce = $nonce,
    t.claim_sequence = $sequence
RETURN t.id AS task_id, t.status AS status,
       g.high_water AS high_water, g.term AS term
"""
def try_claim(driver, claim: FixtureClaim) -> dict | None:
    """Commit only if matched; denied/incomplete attempts rollback everything.

    This executor intentionally uses a disposable graph credential. Direct
    arbitrary Cypher with the same credential is a known bypass/NO-GO.
    """
    arguments = validate(claim)
    with driver.session(database="neo4j") as session:
        tx = session.begin_transaction()
        try:
            record = tx.run(CYPHER, arguments).single()
            if record is None:
                tx.rollback()
                return None
            result = dict(record)
            if (set(result) != {"task_id","status","high_water","term"}
                    or result["task_id"] != claim.task_id
                    or result["status"] != "CLAIMED"
                    or result["high_water"] != claim.sequence
                    or result["term"] != claim.term):
                tx.rollback()
                raise FencedDenied("UNTRUSTED_GRAPH_WRITE_RESPONSE")
            tx.commit()
            return result
        except BaseException:
            tx.rollback()
            raise