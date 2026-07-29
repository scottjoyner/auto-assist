import hashlib
from types import SimpleNamespace

from assistx.controller_runtime import Neo4jControllerStore
from assistx.execution_control import ExecutionControlPlane
from assistx.improvement_cycle import (
    ImprovementCycle,
    build_execution_contract,
    evaluate_completion,
)
from assistx.improvement_runtime import sign_executor_evidence
from assistx.kv_cache import build_manifest
from assistx.recovery_control import Neo4jRecoveryStore, RecoveryControlPlane
from assistx.recovery_runbooks import RecoveryRunbookExecutor, build_runbook, sign_runbook
from assistx.self_healing import SelfHealingController


def test_recovery_lifecycle_canary_with_real_neo4j(seeded_neo4j, monkeypatch, tmp_path):
    neo = seeded_neo4j
    controller_store = Neo4jControllerStore(neo)
    first_lease = controller_store.acquire(
        "canary-controller", "instance-a", now_ms=1000, ttl_ms=1000
    )
    blocked_lease = controller_store.acquire(
        "canary-controller", "instance-b", now_ms=1500, ttl_ms=1000
    )
    failover_lease = controller_store.acquire(
        "canary-controller", "instance-b", now_ms=2001, ttl_ms=1000
    )
    assert first_lease["fencing_token"] == 1
    assert blocked_lease is None
    assert failover_lease["fencing_token"] == 2
    begun = controller_store.begin_tick(
        "canary-controller",
        "instance-b",
        2,
        "canary-tick-1",
        now_ms=2001,
    )
    assert begun["started"] is True
    assert controller_store.finish_tick(
        "canary-controller",
        "instance-b",
        2,
        "canary-tick-1",
        now_ms=2100,
        status="SUCCEEDED",
        result={"reconciled": 1},
    )
    replay = controller_store.begin_tick(
        "canary-controller",
        "instance-b",
        2,
        "canary-tick-1",
        now_ms=2101,
    )
    assert replay["started"] is False
    controller_status = controller_store.list_status(now_ms=2101)
    assert controller_status[0]["checkpoint"]["result"]["reconciled"] == 1

    execution = ExecutionControlPlane()
    with neo._session() as session:
        session.run(
            """
            MERGE (source:SwarmNode {node_id:'node-canary'})
            SET source.status='online', source.weight=1, source.is_blocked=false,
                source.capabilities=['llm','recovery']
            MERGE (target:SwarmNode {node_id:'node-canary-b'})
            SET target.status='online', target.weight=1, target.is_blocked=false,
                target.capabilities=['llm']
            """
        ).consume()
    movable = neo.create_task_with_context(
        title="Migration canary",
        status="READY",
        kind="analysis",
        required_capabilities=["llm"],
        preemptible=True,
        max_migrations=2,
        idempotency_key="migration-canary",
    )
    reservation = neo.reserve_task_allocation(
        task_id=movable["task_id"],
        node_id="node-canary",
        model_id="model-canary",
        snapshot_revision=1,
        actor="canary-operator",
        ttl_seconds=120,
    )
    first_claim = neo.claim_task(
        movable["task_id"], "node-canary", capabilities=["llm"]
    )
    first_claim_id = first_claim["task"]["claim_id"]
    requested = execution.request_preemption(
        neo,
        movable["task_id"],
        "canary-operator",
        reason="move to higher-value node",
        target_agent_id="node-canary-b",
    )
    checkpointed = execution.checkpoint(
        neo,
        movable["task_id"],
        "node-canary",
        first_claim_id,
        checkpoint={"handler": "benchmark", "next_case_index": 1},
        progress=0.5,
        estimated_remaining_seconds=30,
        pause=True,
    )
    stale_checkpoint = execution.checkpoint(
        neo,
        movable["task_id"],
        "node-canary",
        first_claim_id,
        checkpoint={"unsafe": True},
        progress=0.6,
        estimated_remaining_seconds=20,
        pause=False,
    )
    reconciliation = execution.reconcile(
        neo,
        preemption_timeout_seconds=600,
    )
    migrated_task = neo.get_task(movable["task_id"])
    stale_completion = neo.complete_task(
        movable["task_id"],
        "node-canary",
        "DONE",
        claim_id=first_claim_id,
    )
    second_claim = neo.claim_task(
        movable["task_id"], "node-canary-b", capabilities=["llm"]
    )
    completed_after_resume = neo.complete_task(
        movable["task_id"],
        "node-canary-b",
        "DONE",
        claim_id=second_claim["task"]["claim_id"],
    )
    with neo._session() as session:
        old_reservation = session.run(
            """
            MATCH (a:AllocationReservation {id:$reservation_id})
            RETURN a.status AS status
            """,
            {"reservation_id": reservation["reservation"]["id"]},
        ).single()
    assert reservation["reserved"] is True
    assert requested["requested"] is True
    assert checkpointed["paused"] is True
    assert stale_checkpoint["checkpointed"] is False
    assert reconciliation["migrated"] == 1
    assert migrated_task["migration_count"] == 1
    assert old_reservation["status"] == "SUPERSEDED"
    assert stale_completion is None
    assert second_claim["claimed"] is True
    assert completed_after_resume["status"] == "DONE"

    improvement = ImprovementCycle()
    improvement_contract = build_execution_contract(
        repository="auto-assist",
        objective="Add one bounded canary regression",
        allowed_paths=["tests/test_canary_generated.py"],
        verification_commands=[
            ["pytest", "-q", "tests/test_canary_generated.py"]
        ],
        recommended_tier="tool-small",
    )
    improvement_task = neo.create_task_with_context(
        title="Bounded improvement canary",
        status="READY",
        kind="bounded_code_change",
        required_capabilities=["llm", "code_execution"],
        payload={"execution_contract": improvement_contract},
        idempotency_key="bounded-improvement-canary",
    )
    improvement_task_state = neo.get_task(improvement_task["task_id"])
    evaluation = evaluate_completion(
        improvement_task_state,
        requested_status="DONE",
        result={"output": "prose without evidence"},
    )
    recorded = improvement.record_attempt(
        neo,
        improvement_task_state,
        agent_id="small-agent-canary",
        model_id="small-model-canary",
        evaluation=evaluation,
    )
    repair_task_id = improvement.propose_repair(
        neo,
        improvement_task_state,
        evaluation,
    )
    repair_task = neo.get_task(repair_task_id)
    attempt_id = recorded["attempt"]["id"]
    loaded_attempt = improvement.get_attempt(neo, attempt_id)
    promotion_record = improvement.record_promotion(
        neo,
        attempt_id,
        actor="canary-operator",
        reason="Canary records a rejected promotion safely.",
        result={"promoted": False, "reason": "canary_rejection"},
    )
    assert evaluation["effective_status"] == "FAILED"
    assert recorded["profile"]["attempts"] == 1
    assert recorded["profile"]["verified_rate"] == 0.0
    assert loaded_attempt["task"]["id"] == improvement_task["task_id"]
    assert promotion_record["promotion_status"] == "REJECTED"
    assert repair_task["status"] == "PROPOSED"
    assert repair_task["kind"] == "improvement_repair"

    patch = (
        "diff --git a/tests/test_canary_generated.py "
        "b/tests/test_canary_generated.py\n"
    )
    signed_evidence = sign_executor_evidence(
        {
            "evidence_source": "executor",
            "executor_id": "small-agent-canary",
            "worktree_clean_before": True,
            "isolated_worktree": True,
            "scope_validated": True,
            "changed_files": ["tests/test_canary_generated.py"],
            "diff_lines": 2,
            "tools_used": [
                "inspect_file",
                "apply_patch",
                "run_verification",
                "inspect_diff",
            ],
            "verification": [
                {
                    "command": [
                        "pytest",
                        "-q",
                        "tests/test_canary_generated.py",
                    ],
                    "returncode": 0,
                }
            ],
            "summary": "Signed improvement canary.",
            "patch": patch,
            "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        },
        key_id="canary-node-v1",
        secret="canary-node-secret",
    )
    accepted_task = neo.create_task_with_context(
        title="Signed bounded improvement canary",
        status="READY",
        kind="bounded_code_change",
        required_capabilities=["llm", "code_execution"],
        payload={"execution_contract": improvement_contract},
        idempotency_key="signed-bounded-improvement-canary",
    )
    accepted_task_state = neo.get_task(accepted_task["task_id"])
    accepted_evaluation = evaluate_completion(
        accepted_task_state,
        requested_status="DONE",
        result={"completion_envelope": signed_evidence},
        verify_keys={"canary-node-v1": "canary-node-secret"},
    )
    accepted_record = improvement.record_attempt(
        neo,
        accepted_task_state,
        agent_id="small-agent-canary",
        model_id="small-model-canary",
        evaluation=accepted_evaluation,
    )
    claimed_promotion = improvement.claim_promotion(
        neo,
        accepted_record["attempt"]["id"],
        actor="canary-operator",
        fingerprint=signed_evidence["patch_sha256"],
    )
    assert accepted_evaluation["accepted"] is True
    assert claimed_promotion["promotion_status"] == "PROMOTING"

    with neo._session() as session:
        session.run(
            """
            MERGE (n:SwarmNode {node_id:'node-canary'})
            SET n.status='online', n.weight=1, n.is_blocked=false
            """
        ).consume()

    diagnosis = {
        "diagnosis_id": "diag-canary",
        "incident_key": "incident-canary",
        "node_id": "node-canary",
        "recommended_recovery": {
            "action": "restore_service",
            "risk": "critical",
            "verify_after": ["service_online"],
            "rollback": "restore_previous_control_state",
        },
    }
    monkeypatch.setenv("ASSISTX_RECOVERY_EXECUTION_ENABLED", "true")
    control = RecoveryControlPlane()
    store = Neo4jRecoveryStore(neo)
    proposal = control.propose(store, diagnosis, "canary-operator")
    control.approve(store, proposal["id"], proposal["fingerprint"], "canary-operator")

    def dispatch(plan):
        runbook = sign_runbook(
            build_runbook(plan, proposal["id"]),
            key_id="canary-v1",
            secret="canary-secret",
        )
        created = neo.create_task_with_context(
            title="Canary recovery",
            task_type="fleet_recovery",
            status="READY",
            kind="recovery_restore_service",
            required_capabilities=["recovery"],
            target_agent_id="node-canary",
            priority="HIGH",
            payload={"runbook": runbook, "execution_mode": "typed_runbook"},
            idempotency_key=f"canary:{proposal['id']}",
        )
        return {"task_id": created["task_id"]}

    dispatched = control.execute(store, proposal["id"], "canary-operator", dispatch)
    task = neo.get_task(dispatched["task_id"])
    runbook = __import__("json").loads(task["payload_json"])["runbook"]
    responses = iter([(503, {}), (200, {"data": []}), (200, {"data": []})])
    executor = RecoveryRunbookExecutor(
        node_id="node-canary",
        lmstudio_url="http://canary",
        state_dir=str(tmp_path),
        http=lambda *_args, **_kwargs: next(responses),
        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="active", stderr=""),
        sleeper=lambda _: None,
        env={"FLEET_RECOVERY_SERVICE_ALIASES": '{"inference":"canary.service"}'},
        verify_keys={"canary-v1": "canary-secret"},
    )
    result = executor.execute(runbook)
    outcome = control.record_outcome(
        store,
        proposal["id"],
        "node-canary",
        verified=result["ok"],
        evidence=result,
    )

    assert dispatched["executed"] is True
    assert result["status"] == "verified"
    assert outcome["status"] == "VERIFIED"
    assert len(store.list_audit()) >= 3

    cache_manifest = build_manifest(
        {
            "cache_id": "cache-canary",
            "prefix_id": "prefix-" + "a" * 64,
            "node_id": "node-canary",
            "endpoint_id": "node-canary.lmstudio",
            "model_id": "model-canary",
            "runtime": "lmstudio",
            "compatibility": {
                "model_artifact_hash": "canary-weights",
                "model_id": "model-canary",
                "model_quantization": "Q4_K_M",
                "kv_k_quantization": "q8_0",
                "kv_v_quantization": "q8_0",
                "tokenizer_hash": "canary-tokenizer",
                "chat_template_hash": "canary-template",
                "adapter_hash": None,
                "runtime": "lmstudio",
                "runtime_version": "canary",
                "cache_format_version": "resident-v1",
                "context_length": 32768,
                "rope_config_hash": "canary-rope",
            },
            "privacy_scope": "project",
            "scope_id": "canary",
            "token_count": 4096,
            "bytes": 1024,
            "storage_tier": "host",
            "portable": False,
            "ttl_seconds": 120,
        }
    )
    stored_cache = neo.upsert_kv_cache_manifest(
        cache_manifest,
        actor="node:node-canary",
    )
    cache_event = neo.record_kv_cache_event(
        "cache-canary",
        outcome="HIT",
        node_id="node-canary",
        prefix_id=cache_manifest["prefix_id"],
        tokens_saved=4096,
        prefill_ms_saved=2500,
        actor="node:node-canary",
    )
    cache_status = neo.kv_cache_status()
    assert stored_cache["compatibility_fingerprint"]
    assert cache_event["manifest"]["hit_count"] == 1
    assert cache_status["summary"]["hits"] >= 1

    alloc_task = neo.create_task_with_context(
        title="Allocation canary",
        task_type="swarm_task",
        status="READY",
        kind="analysis",
        required_capabilities=["llm"],
        target_agent_id=None,
        priority="HIGH",
        payload={"queue_class": "interactive"},
        idempotency_key="allocation-canary",
    )
    reservation = neo.reserve_task_allocation(
        task_id=alloc_task["task_id"],
        node_id="node-canary",
        model_id="model-canary",
        cache_id="cache-canary",
        snapshot_revision=1,
        actor="canary-operator",
        ttl_seconds=120,
    )
    wrong = neo.claim_task(
        alloc_task["task_id"],
        "wrong-node",
        capabilities=["llm"],
    )
    correct = neo.claim_task(
        alloc_task["task_id"],
        "node-canary",
        capabilities=["llm"],
    )

    assert reservation["reserved"] is True
    assert reservation["reservation"]["cache_id"] == "cache-canary"
    assert wrong["claimed"] is False
    assert correct["claimed"] is True

    release_task = neo.create_task_with_context(
        title="Reservation release canary",
        task_type="swarm_task",
        status="READY",
        kind="analysis",
        required_capabilities=["llm"],
        target_agent_id=None,
        priority="MEDIUM",
        payload={"queue_class": "interactive"},
        idempotency_key="allocation-release-canary",
    )
    releasable = neo.reserve_task_allocation(
        task_id=release_task["task_id"],
        node_id="node-canary",
        model_id="model-canary",
        snapshot_revision=2,
        actor="canary-operator",
        ttl_seconds=120,
    )
    released = neo.release_task_allocation(
        releasable["reservation"]["id"],
        "canary-operator",
    )
    assert released["released"] is True
    assert neo.get_task(release_task["task_id"]).get("target_agent_id") is None

    healing = SelfHealingController()
    controlled = healing.set_node_control(
        neo,
        "node-canary",
        "canary-operator",
        mode="maintenance",
        reason="canary maintenance",
        ttl_seconds=60,
    )
    assert controlled["updated"] is True
    with neo._session() as session:
        session.run(
            """
            MATCH (n:SwarmNode {node_id:'node-canary'})
            SET n.control_expires_at_ts=timestamp()-1
            """
        ).consume()
    controls = healing.list_node_controls(neo)
    node_control = next(
        row for row in controls["nodes"] if row["node_id"] == "node-canary"
    )
    assert node_control["blocked"] is False
    assert any(
        event["action"] == "expire_control" for event in controls["audit"]
    )
