from types import SimpleNamespace

from assistx.controller_runtime import Neo4jControllerStore
from assistx.execution_control import ExecutionControlPlane
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
            MERGE (n:SwarmNode {node_id:'node-canary-b'})
            SET n.status='online', n.weight=1, n.is_blocked=false
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
    assert requested["requested"] is True
    assert checkpointed["paused"] is True
    assert stale_checkpoint["checkpointed"] is False
    assert reconciliation["migrated"] == 1
    assert migrated_task["migration_count"] == 1
    assert stale_completion is None
    assert second_claim["claimed"] is True
    assert completed_after_resume["status"] == "DONE"

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
