"""Read-only B01 snapshot classifier: never decides to retry/clear/failover."""
from dataclasses import replace
from pathlib import Path
import pytest
from trace_b01_readonly_effect_reconcile import (
    GraphObservation,WitnessObservation,classify,
)

INSTANCE='a'*64
TASK='fixture-task-valid123'
AGENT='fixture-agent-valid123'
NONCE='nonce-valid123'
W=WitnessObservation(instance=INSTANCE,task_id=TASK,agent_id=AGENT,
    term=1,high_water=1,pending=True,pending_term=1,
    pending_sequence=1,pending_nonce=NONCE)
G=GraphObservation(instance=INSTANCE,task_id=TASK,agent_id=AGENT,
    term=1,high_water=1,task_status='CLAIMED',
    claim_term=1,claim_sequence=1,claim_nonce=NONCE)


def status(w=W,g=G,*,floor=0):
    result=classify(w,g,independently_retained_floor=floor)
    assert result['production_decision']=='HOLD'
    assert result['graph_effect_proven'] is False
    assert result['trusted_monotonic_witness_proven'] is False
    assert result['independent_postcommit_receipt_proven'] is False
    assert result['automatic_reexecute_authorized'] is False
    assert result['automatic_pending_clear_authorized'] is False
    assert result['failover_authorized'] is False
    assert result['production_admission_authorized'] is False
    return result['diagnostic']


def test_matching_observations_do_not_count_as_postcommit_proof():
    assert status()=='MATCHING_UNATTESTED_SNAPSHOTS'


def test_disposable_outage_late_pending_never_authorizes_reexecution():
    original=replace(G,high_water=0,task_status='READY',
        claim_term=None,claim_sequence=None,claim_nonce=None)
    assert status(g=original)=='GRAPH_REWIND_OR_UNCERTAIN_UNCOMMITTED_EFFECT'


def test_disposable_graph_only_restore_detected_with_surviving_witness():
    restored=replace(G,high_water=0,task_status='READY',
        claim_term=None,claim_sequence=None,claim_nonce=None)
    assert status(g=restored,floor=1)=='GRAPH_REWIND_OR_UNCERTAIN_UNCOMMITTED_EFFECT'


def test_coordinated_restore_is_indistinguishable_from_first_use():
    rolled_w=replace(W,high_water=0,pending=False,pending_term=None,
        pending_sequence=None,pending_nonce=None)
    rolled_g=replace(G,high_water=0,task_status='READY',
        claim_term=None,claim_sequence=None,claim_nonce=None)
    assert status(w=rolled_w,g=rolled_g)=='EMPTY_OR_COHERENTLY_RESTORED_INDETERMINATE'
    # A separately retained floor *might* help detect it; no resource-side
    # compare-and-swap protects any mutation here, so this remains HOLD.
    assert status(w=rolled_w,g=rolled_g,floor=1)==(
        'WITNESS_REGRESSED_BELOW_RETAINED_FLOOR')

@pytest.mark.parametrize('w,g,expected',[
    (None,G,'OBSERVATION_UNAVAILABLE'),
    (W,None,'OBSERVATION_UNAVAILABLE'),
    (replace(W,task_id='fixture-task-foreign123'),G,'SCOPE_OR_IDENTITY_FORK'),
    (replace(W,instance='b'*64),G,'SCOPE_OR_IDENTITY_FORK'),
    (replace(W,term=2),G,'TERM_DISAGREEMENT'),
    (replace(W,high_water=0,pending=False,pending_term=None,
             pending_sequence=None,pending_nonce=None),G,
     'UNWITNESSED_GRAPH_ADVANCE_OR_WITNESS_ROLLBACK'),
    (replace(W,pending=False,pending_term=None,pending_sequence=None,
             pending_nonce=None),G,'WITNESS_PENDING_EVIDENCE_MISSING'),
    (replace(W,pending_sequence=2),G,'WITNESS_PENDING_FORK'),
    (replace(G,claim_nonce='nonce-different123'),W,'SCOPE_OR_IDENTITY_FORK'),
])
def test_ambiguous_or_contradictory_evidence_is_hold(w,g,expected):
    # Note: one case intentionally swaps argument order to prove diagnostic
    # input shape is checked, not inferred from an object's attribute names.
    if type(w) is GraphObservation:
        assert status(w=w,g=g)=='MALFORMED_OR_UNPINNED_OBSERVATION'
    elif w is not None and g is not None and getattr(g,'claim_nonce',None)=='nonce-different123':
        assert status(w=w,g=g)=='GRAPH_WITNESS_CLAIM_MISMATCH'
    else:
        assert status(w=w,g=g)==expected


@pytest.mark.parametrize('w,g,floor',[
    (replace(W,high_water=True),G,0),
    (W,replace(G,high_water=True),0),
    (W,G,True), (W,G,-1),
    (W,replace(G,task_status='unexpected'),0),
    (replace(W,pending='yes'),G,0),
    (replace(W,pending_nonce='raw!'),G,0),
    (replace(W,instance='invalid'),G,0),
    (replace(W,term=0),G,0),
    (replace(W,pending=False),G,0),
])
def test_invalid_sources_never_produce_a_permit(w,g,floor):
    assert status(w=w,g=g,floor=floor)=='MALFORMED_OR_UNPINNED_OBSERVATION'


def test_unclaimed_graph_at_equal_highwater_is_pending_unknown():
    ready=replace(G,task_status='READY',claim_term=None,
                  claim_sequence=None,claim_nonce=None)
    assert status(g=ready)=='UNCERTAIN_GRAPH_COMMIT_PENDING_WITNESS'


def test_source_contains_no_graph_mutation_or_network_or_clearance():
    source=Path(__file__).with_name('trace_b01_readonly_effect_reconcile.py').read_text()
    assert 'GraphDatabase.driver' not in source
    assert 'docker(' not in source
    assert 'os.system' not in source
    assert 'HTTP' not in source
    assert "'automatic_pending_clear_authorized':False" in source
    assert "'production_decision':'HOLD'" in source
    assert 'MATCHING_UNATTESTED_SNAPSHOTS' in source