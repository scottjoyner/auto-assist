"""Read-only safety proof on the disposable Neo4j harness, never production.

Intentionally simulate application failure after the real record_event commits
a synthetic EventEnvelope and before graph projection. Confirm retry is deduped
without repairing the graph. No manual replay/write authorization is exposed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

SRC=Path(__file__).resolve().parents[1]/"src"
sys.path.insert(0,str(SRC))

from assistx import swarm_core  # noqa: E402


class SyntheticNeoProxy:
    def __init__(self,driver):
        self.driver=driver
    def _session(self):
        return self.driver.session(database="neo4j")


def synthetic_projection_gap_scenarios(driver,prefix):
    if not prefix.startswith("scratch-acceptance-"):
        raise ValueError("non-synthetic source refused")
    nonce=uuid4().hex
    event_id=prefix+":gap-event-"+nonce
    node_id=prefix+":gap-node-"+nonce
    event={
        "event_id":event_id,
        "event_type":"swarm.node.heartbeat",
        "source_repo":"scratch-acceptance",
        "source_service":"scratch-acceptance",
        "node_id":node_id,
        "occurred_at":"2026-10-12T14:00:00Z",
        "idempotency_key":prefix+":gap-key-"+nonce,
        "correlation_id":str(uuid4()),
        "schema_version":"1.0",
        "subject":{"kind":"synthetic","id":node_id},
        "payload":{"node_id":node_id,"services":[]},
        "artifact_refs":[],
        "privacy":{"pii":False,"privacy_class":"internal","retention_class":"short"},
    }
    proxy=SyntheticNeoProxy(driver)
    outcome={}
    with patch.object(swarm_core,"reconcile_event",
                      side_effect=RuntimeError("synthetic-only-interrupted-projection")):
        try:
            swarm_core.record_event(proxy,event)
            outcome["interrupted_projection_raises_instead_of_reporting_success"]=False
        except RuntimeError:
            outcome["interrupted_projection_raises_instead_of_reporting_success"]=True

    with driver.session(database="neo4j") as session:
        proof=session.run("""
        OPTIONAL MATCH (e:EventEnvelope {event_id:$id})
        WITH count(e) AS event_count
        OPTIONAL MATCH (n:SwarmNode {node_id:$node})
        RETURN event_count, count(n) AS node_count
        """,id=event_id,node=node_id).single()
        outcome["event_persisted_before_projection_committed"]=(
            proof is not None and proof["event_count"]==1
            and proof["node_count"]==0
        )
    unexpectedly_called=[]
    with patch.object(swarm_core,"reconcile_event",
                      side_effect=lambda *a:unexpectedly_called.append(True)):
        retry=swarm_core.record_event(proxy,event)
    outcome["same_envelope_retry_deduped_without_projection"]=(
        retry.get("accepted") is True
        and retry.get("deduped") is True
        and retry.get("graph_reconciled") is False
        and unexpectedly_called==[]
    )
    with driver.session(database="neo4j") as session:
        proof=session.run("""
        MATCH (e:EventEnvelope {event_id:$id})
        RETURN count(e) AS ledger_count
        """,id=event_id).single()
        node=session.run("""
        MATCH (n:SwarmNode {node_id:$id}) RETURN count(n) AS n
        """,id=node_id).single()
        outcome["event_remains_without_graph_projection_after_retry"]=(
            proof is not None and proof["ledger_count"]==1
            and node is not None and node["n"]==0
        )
    return outcome
