from __future__ import annotations

import json
import os
import time
from typing import Any


class SelfHealingController:
    def __init__(self) -> None:
        self.quarantine_enabled = os.getenv("ASSISTX_AUTO_QUARANTINE_ENABLED", "false").lower() in {"1", "true", "yes"}

    def status(self) -> dict[str, Any]:
        return {
            "automatic_detection": True,
            "automatic_incident_materialization": True,
            "automatic_quarantine": self.quarantine_enabled,
            "automatic_restart": False,
            "automatic_rejoin": False,
        }

    def reconcile(self, neo: Any, plan: dict[str, Any]) -> dict[str, Any]:
        now = int(time.time())
        keys = []
        with neo._session() as session:
            for incident in plan.get("incidents") or []:
                key = str(incident.get("incident_key") or "")
                if not key:
                    continue
                keys.append(key)
                session.run(
                    """
                    MERGE (i:FleetIncident {incident_key:$key})
                    ON CREATE SET i.id=randomUUID(), i.created_at_ts=$now,
                                  i.status='OPEN'
                    SET i.node_id=$node_id, i.model_id=$model_id,
                        i.incident_type=$incident_type, i.severity=$severity,
                        i.recommended_action=$recommended_action,
                        i.detail=$detail, i.last_seen_at_ts=$now,
                        i.updated_at_ts=$now
                    """,
                    {
                        "key": key,
                        "now": now,
                        "node_id": incident.get("node_id"),
                        "model_id": incident.get("model_id"),
                        "incident_type": incident.get("incident_type"),
                        "severity": incident.get("severity"),
                        "recommended_action": incident.get("recommended_action"),
                        "detail": incident.get("detail"),
                    },
                ).consume()
            # Resolve incidents absent from the newest full detection plan.
            session.run(
                """
                MATCH (i:FleetIncident)
                WHERE i.status='OPEN' AND NOT i.incident_key IN $keys
                SET i.status='RESOLVED', i.resolved_at_ts=$now, i.updated_at_ts=$now
                """,
                {"keys": keys, "now": now},
            ).consume()
        quarantined = []
        if self.quarantine_enabled:
            for incident in plan.get("incidents") or []:
                if incident.get("severity") == "critical" and incident.get("recommended_action") == "quarantine":
                    result = self.quarantine(neo, str(incident["incident_key"]), "self-healing-controller")
                    if result.get("quarantined"):
                        quarantined.append(incident["node_id"])
        return {"reconciled": len(keys), "auto_quarantined": quarantined, **self.status()}

    def quarantine(self, neo: Any, incident_key: str, actor: str) -> dict[str, Any]:
        if actor == "self-healing-controller" and not self.quarantine_enabled:
            return {"quarantined": False, "blocked": True, "reason": "auto_quarantine_disabled"}
        now = int(time.time())
        with neo._session() as session:
            row = session.run(
                """
                MATCH (i:FleetIncident {incident_key:$key})
                WHERE i.status IN ['OPEN','ACKNOWLEDGED']
                  AND i.severity='critical'
                  AND i.recommended_action='quarantine'
                OPTIONAL MATCH (n:SwarmNode {node_id:i.node_id})
                SET i.status='QUARANTINED', i.quarantined_by=$actor,
                    i.quarantined_at_ts=$now, i.updated_at_ts=$now,
                    n.is_blocked=true, n.control_mode='maintenance',
                    n.control_reason='fleet_incident', n.updated_at_ts=$now
                RETURN i.node_id AS node_id
                """,
                {"key": incident_key, "actor": actor, "now": now},
            ).single()
        return {"quarantined": bool(row), "node_id": row["node_id"] if row else None}

    def rejoin(self, neo: Any, node_id: str, actor: str, health_plan: dict[str, Any]) -> dict[str, Any]:
        active = [
            row for row in health_plan.get("incidents") or []
            if row.get("node_id") == node_id and row.get("severity") == "critical"
        ]
        if active:
            return {"rejoined": False, "blocked": True, "reason": "critical health evidence remains", "incidents": active}
        now = int(time.time())
        with neo._session() as session:
            row = session.run(
                """
                MATCH (n:SwarmNode {node_id:$node_id})
                SET n.is_blocked=false, n.control_mode='enabled',
                    n.control_reason=null, n.rejoined_by=$actor,
                    n.rejoined_at_ts=$now, n.updated_at_ts=$now
                WITH n
                OPTIONAL MATCH (i:FleetIncident {node_id:$node_id})
                WHERE i.status='QUARANTINED'
                SET i.status='RESOLVED', i.resolved_by=$actor,
                    i.resolved_at_ts=$now, i.updated_at_ts=$now
                RETURN n.node_id AS node_id
                """,
                {"node_id": node_id, "actor": actor, "now": now},
            ).single()
        return {"rejoined": bool(row), "node_id": node_id}

    def list_incidents(self, neo: Any, limit: int = 200) -> list[dict[str, Any]]:
        with neo._session() as session:
            rows = session.run(
                """
                MATCH (i:FleetIncident)
                RETURN i ORDER BY
                  CASE i.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                  coalesce(i.updated_at_ts,0) DESC
                LIMIT $limit
                """,
                {"limit": limit},
            )
            return [dict(row["i"]) for row in rows]
