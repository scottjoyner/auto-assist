from types import SimpleNamespace

from assistx.recovery_control import Neo4jRecoveryStore, RecoveryControlPlane
from assistx.recovery_runbooks import RecoveryRunbookExecutor, build_runbook, sign_runbook


def test_recovery_lifecycle_canary_with_real_neo4j(seeded_neo4j, monkeypatch, tmp_path):
    neo = seeded_neo4j
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
