"""Offline claim fencing contract checks; no Bolt and no Docker."""
from dataclasses import replace
from pathlib import Path
import pytest
from trace_b01_disposable_claim_fence import (
    CYPHER, SCOPE, FixtureClaim, FencedDenied, validate,
)

GOOD = FixtureClaim(
    task_id="fixture-task-valid123",
    agent_id="fixture-agent-valid123",
    term=1, sequence=1, nonce="nonce-valid123",
    authority_digest="a"*64,
    recovery_identity_verified=True,
)

def test_valid_synthetic_claim_shape():
    d = validate(GOOD)
    assert set(d) == {
        "scope", "task_id", "agent_id", "term",
        "sequence", "nonce", "authority_digest",
        "recovery_identity_verified",
    }
    assert d["scope"] == SCOPE
    assert d["sequence"] == 1

@pytest.mark.parametrize("field,value", [
    ("task_id","not-a-fixture"),
    ("agent_id","random-agent"),
    ("term",0),("term",True),("term","1"),
    ("sequence",0),("sequence",True),("sequence",-2),
    ("nonce","arbitrary"),("nonce","nonce-x y"),
    ("authority_digest","not-digest"),
    ("recovery_identity_verified",None),
    ("recovery_identity_verified",1),
])
def test_invalid_claims_fail_before_graph(field,value):
    with pytest.raises(FencedDenied):
        validate(replace(GOOD,**{field:value}))
def test_fixed_statement_serializes_and_requires_stable_resource_checks():
    assert "MATCH (g:FixtureFence {scope:$scope})" in CYPHER
    assert "SET g.mutex = coalesce(g.mutex,0) + 1" in CYPHER
    assert "g.high_water + 1 = $sequence" in CYPHER
    assert "g.term = $term" in CYPHER
    assert "g.authority_digest = $authority_digest" in CYPHER
    assert "NOT $nonce IN g.seen_nonces" in CYPHER
    assert "t.status = 'READY'" in CYPHER
    assert "t.owner_id = $agent_id" in CYPHER
    assert "$recovery_identity_verified = true" in CYPHER
    assert "SET g.high_water = $sequence" in CYPHER
    assert "t.claimed_by = $agent_id" in CYPHER
    assert "RETURN t.id" in CYPHER
    assert "MERGE" not in CYPHER
    assert "DELETE" not in CYPHER

def test_failed_claim_rolls_back_lock_and_cannot_count_as_grant():
    source=Path(__file__).with_name(
        "trace_b01_disposable_claim_fence.py").read_text()
    assert "if record is None:" in source
    assert "tx.rollback()" in source
    assert "tx.commit()" in source
    assert "class FixtureClaim" in source
    assert "GraphDatabase.driver" not in source
    assert "neo4j:" not in source
    assert "app.post(" not in source

def test_no_production_scope_accepted():
    with pytest.raises(FencedDenied):
        validate(replace(GOOD,task_id="task-real-2026"))


def test_physical_probe_is_opt_in_and_never_mutates_live_runtime():
    source=Path(__file__).with_name(
        "probe_trace_b01_disposable_claim_fence.py").read_text()
    for required in (
        "EXPLICIT_DISPOSABLE_FENCE_OPT_IN_REQUIRED",
        '"--internal","--driver","bridge"',
        '"--pull","never"',
        '"--memory","2300m"',
        '"--pids-limit","256"',
        'docker("rm","-f","-v",graph',
        'docker("network","rm",network',
        "UNSAFE_DISPOSABLE_GRAPH",
        "arbitrary_writer_cypher_bypasses_fixed_statement",
        "graph_only_restore_reaccepts_old_proof",
        "independent_rollback_anchor_proven",
        "production_authority_signature_verified",
        "production_task_claim_cutover_authorized",
    ):
        assert required in source
    for unsafe in (
        "assistx-api", "assistx-worker",
        "docker network disconnect", "docker restart", "docker stop",
        "Tailscale", "NEO4J_PASSWORD", "neo4j-ssd.yml",
    ):
        assert unsafe not in source

def test_denied_writes_are_transactionally_rolled_back():
    src=Path(__file__).with_name(
        "trace_b01_disposable_claim_fence.py").read_text()
    assert src.index("SET g.mutex =") < src.index("g.high_water + 1")
    assert src.index("g.high_water + 1") < src.index("SET g.high_water =")
    assert "if record is None:" in src
    assert "tx.rollback()" in src
    assert "tx.commit()" in src