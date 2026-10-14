"""B01 fixture-only READ-ONLY reconciliation: diagnostic HOLD, never authority.

Inputs are ASSERTED observations, not separately certified post-COMMIT
receipts. Matching two mutable snapshots cannot distinguish real first use
from coordinated rollback. All classifications deny retry/failover/release.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

HEX = re.compile(r'[a-f0-9]{64}')
TASK = re.compile(r'fixture-task-[A-Za-z0-9_-]{4,48}')
AGENT = re.compile(r'fixture-agent-[A-Za-z0-9_-]{4,48}')
NONCE = re.compile(r'nonce-[A-Za-z0-9_-]{6,48}')


@dataclass(frozen=True)
class WitnessObservation:
    instance: str
    task_id: str
    agent_id: str
    term: int
    high_water: int
    pending: bool
    pending_term: int | None
    pending_sequence: int | None
    pending_nonce: str | None


@dataclass(frozen=True)
class GraphObservation:
    instance: str
    task_id: str
    agent_id: str
    term: int
    high_water: int
    task_status: str
    claim_term: int | None
    claim_sequence: int | None
    claim_nonce: str | None


def _integer(value, *, minimum):
    return type(value) is int and value >= minimum


def _valid(w, g, floor):
    if (type(w) is not WitnessObservation or type(g) is not GraphObservation
            or type(floor) is not int or floor < 0):
        return False
    for item in (w, g):
        if (type(item.instance) is not str or not HEX.fullmatch(item.instance)
                or type(item.task_id) is not str or not TASK.fullmatch(item.task_id)
                or type(item.agent_id) is not str or not AGENT.fullmatch(item.agent_id)
                or not _integer(item.term,minimum=1)
                or not _integer(item.high_water,minimum=0)):
            return False
    if type(w.pending) is not bool or g.task_status not in ('READY','CLAIMED'):
        return False
    if w.pending:
        if (not _integer(w.pending_term,minimum=1)
                or not _integer(w.pending_sequence,minimum=1)
                or type(w.pending_nonce) is not str
                or not NONCE.fullmatch(w.pending_nonce)):
            return False
    elif any(v is not None for v in (
            w.pending_term,w.pending_sequence,w.pending_nonce)):
        return False
    if g.task_status=='CLAIMED':
        if (not _integer(g.claim_term,minimum=1)
                or not _integer(g.claim_sequence,minimum=1)
                or type(g.claim_nonce) is not str
                or not NONCE.fullmatch(g.claim_nonce)):
            return False
    elif any(v is not None for v in (
            g.claim_term,g.claim_sequence,g.claim_nonce)):
        return False
    return True


def classify(witness: WitnessObservation | None,
             graph: GraphObservation | None,
             *, independently_retained_floor: int=0) -> dict:
    """Classify untrusted synthetic snapshots; NEVER return a write permit.

    The optional floor is an externally retained *claim*, not proof of truly
    monotonic hardware custody. Passing it never authorizes any operation.
    """
    state='UNKNOWN_UNATTESTED'
    if witness is None or graph is None:
        state='OBSERVATION_UNAVAILABLE'
    elif not _valid(witness,graph,independently_retained_floor):
        state='MALFORMED_OR_UNPINNED_OBSERVATION'
    elif (witness.instance!=graph.instance
          or witness.task_id!=graph.task_id
          or witness.agent_id!=graph.agent_id):
        state='SCOPE_OR_IDENTITY_FORK'
    elif witness.high_water<independently_retained_floor:
        state='WITNESS_REGRESSED_BELOW_RETAINED_FLOOR'
    elif graph.high_water>witness.high_water:
        state='UNWITNESSED_GRAPH_ADVANCE_OR_WITNESS_ROLLBACK'
    elif graph.high_water<witness.high_water:
        state='GRAPH_REWIND_OR_UNCERTAIN_UNCOMMITTED_EFFECT'
    elif graph.term!=witness.term:
        state='TERM_DISAGREEMENT'
    elif witness.high_water==0 and graph.task_status=='READY' and not witness.pending:
        state='EMPTY_OR_COHERENTLY_RESTORED_INDETERMINATE'
    elif not witness.pending:
        state='WITNESS_PENDING_EVIDENCE_MISSING'
    elif (witness.pending_sequence!=witness.high_water
          or witness.pending_term!=witness.term):
        state='WITNESS_PENDING_FORK'
    elif graph.task_status!='CLAIMED':
        state='UNCERTAIN_GRAPH_COMMIT_PENDING_WITNESS'
    elif (graph.claim_sequence!=witness.pending_sequence
          or graph.claim_term!=witness.pending_term
          or graph.claim_nonce!=witness.pending_nonce):
        state='GRAPH_WITNESS_CLAIM_MISMATCH'
    else:
        # Even when all fields agree, neither observation is independently
        # certified post-COMMIT. Simultaneous rollback is indistinguishable.
        state='MATCHING_UNATTESTED_SNAPSHOTS'
    return {
        'schema':'assistx-b01-readonly-effect-reconcile-v0',
        'diagnostic':state,
        'graph_effect_proven':False,
        'trusted_monotonic_witness_proven':False,
        'independent_postcommit_receipt_proven':False,
        'automatic_reexecute_authorized':False,
        'automatic_pending_clear_authorized':False,
        'failover_authorized':False,
        'production_admission_authorized':False,
        'production_decision':'HOLD',
    }