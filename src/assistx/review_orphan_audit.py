"""Bounded read-only legacy review provenance audit (research, never reconciliation).

No CREATE/SET/MERGE/DELETE, no raw Task IDs in returned summaries. No API,
background job or dispatcher calls this helper. A clean sample is NOT a
release gate: external effects, privileged Bolt writers and archived records
are outside its visibility.
"""
from __future__ import annotations

from collections import Counter
import json
from typing import Any

from neo4j import Query, READ_ACCESS


class AuditIncomplete(RuntimeError):
    """A graph read or returned schema is untrustworthy; do not imply PASS."""


_REVIEWS = """
MATCH (r:Task {kind:'intent_review'})
RETURN r.id AS id, r.status AS status, r.review_decision AS decision
LIMIT $max
"""

_CHILDREN = """
MATCH (c:Task {kind:'approved_intent'})
RETURN c.id AS id, c.status AS status,
       c.payload_json AS payload, c.approved_from_review_task AS direct_source
LIMIT $max
"""

_OUTBOXES = """
MATCH (o:ReviewApprovalOutbox)
RETURN o.id AS id, o.review_task_id AS review_id,
       o.candidate_task_id AS child_id, o.status AS status,
       o.verified_operator AS verified_operator,
       o.signed_admission AS signed_admission
LIMIT $max
"""

_REVIEW_LINKS = """
UNWIND $ids AS id
MATCH (r:Task {id:id})
OPTIONAL MATCH (r)-[:APPROVED_AS]->(c:Task)
RETURN id, count(DISTINCT r) AS review_rows,
       count(DISTINCT c) AS child_links
"""

_CHILD_LINKS = """
UNWIND $pairs AS pair
OPTIONAL MATCH (r:Task {id:pair.review_id})
WITH pair, collect(r) AS reviews
UNWIND CASE WHEN size(reviews)=0 THEN [null] ELSE reviews END AS r
OPTIONAL MATCH (r)-[:APPROVED_AS]->(c:Task {id:pair.child_id})
RETURN pair.child_id AS id,
       count(DISTINCT r) AS review_rows,
       count(DISTINCT c) AS linked_children,
       collect(DISTINCT r.review_decision) AS decisions,
       collect(DISTINCT r.status) AS review_statuses
"""

_OUTBOX_LINKS = """
UNWIND $entries AS entry
OPTIONAL MATCH (r:Task {id:entry.review_id})-[link:HAS_APPROVAL_OUTBOX]->
    (o:ReviewApprovalOutbox {id:entry.outbox_id})
OPTIONAL MATCH (r)-[:APPROVED_AS]->(c:Task {id:entry.child_id})
RETURN entry.outbox_id AS id,
       count(DISTINCT link) AS outbox_links,
       count(DISTINCT c) AS approved_children
"""


def _rows(session: Any, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    # Real Neo4j session MUST use READ_ACCESS, and the Cypher here has no
    # dynamic interpolation or writer commands.
    return [dict(row) for row in session.run(Query(statement, timeout=8), params)]


def _pointer(child: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return source review ID, or a structured reason it cannot be trusted."""
    direct = child.get("direct_source")
    if direct is not None and (not isinstance(direct, str) or not direct.strip()):
        return None, "invalid_direct_pointer"
    raw = child.get("payload")
    if raw is None:
        payload = {}
    elif not isinstance(raw, str) or len(raw) > 65536:
        return None, "malformed_payload"
    else:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return None, "malformed_payload"
        if not isinstance(payload, dict):
            return None, "malformed_payload"
    embedded = payload.get("approved_from_review_task")
    if embedded is not None and (
        not isinstance(embedded, str) or not embedded.strip()
    ):
        return None, "invalid_embedded_pointer"
    if direct and embedded and direct != embedded:
        return None, "conflicting_source_pointers"
    return (direct or embedded), None


def _count_status(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(
        r.get(key) if isinstance(r.get(key), str) and r.get(key) else "missing"
        for r in records
    ).items()))


def _require_matching_ids(records: list[dict[str, Any]], key: str) -> None:
    if len(records) != len({row.get(key) for row in records}):
        raise AuditIncomplete("duplicate_id_in_bounded_sample")
    if any(not isinstance(row.get(key), str) or not row[key].strip()
           for row in records):
        raise AuditIncomplete("missing_id_in_bounded_sample")


def audit_review_provenance(
    driver: Any, *, database: str | None = None, limit: int = 150,
) -> dict[str, Any]:
    """Emit aggregate-only HOLD results from capped, read-only graph queries.

    A cap hit means the audit is INCOMPLETE; zero suspicious records never
    certifies global consistency, authenticated approval, or safe dispatch.
    """
    if type(limit) is not int or not 1 <= limit <= 300:
        raise ValueError("invalid_bounded_audit_limit")
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        sets: dict[str, list[dict[str, Any]]] = {}
        truncation: dict[str, bool] = {}
        for name, query in (
            ("reviews", _REVIEWS), ("children", _CHILDREN), ("outboxes", _OUTBOXES),
        ):
            result = _rows(session, query, {"max":limit + 1})
            truncation[name] = len(result) > limit
            sets[name] = result[:limit]
        reviews, children, outboxes = (
            sets["reviews"], sets["children"], sets["outboxes"]
        )
        for group in (reviews, children, outboxes):
            _require_matching_ids(group, "id")

        review_approval_missing = 0
        if reviews:
            review_ids = [row["id"] for row in reviews]
            links = _rows(session, _REVIEW_LINKS, {"ids":review_ids})
            if len(links) != len(review_ids):
                raise AuditIncomplete("ambiguous_review_relation_sample")
            by_id = {row["id"]:row for row in links}
            for row in reviews:
                if row.get("decision") in ("approved", "approved_held"):
                    edge = by_id.get(row["id"])
                    if edge is None or edge["review_rows"] != 1 or edge["child_links"] != 1:
                        review_approval_missing += 1

        pointer_errors: Counter[str] = Counter()
        child_refs = []
        for row in children:
            ref, error = _pointer(row)
            if error is not None:
                pointer_errors[error] += 1
            elif ref is None:
                pointer_errors["no_source_pointer"] += 1
            else:
                child_refs.append({"child_id":row["id"], "review_id":ref})

        child_findings: Counter[str] = Counter()
        if child_refs:
            linked = _rows(session, _CHILD_LINKS, {"pairs":child_refs})
            if len(linked) != len(child_refs):
                raise AuditIncomplete("ambiguous_child_relation_sample")
            by_id = {row["id"]:row for row in linked}
            if len(by_id) != len(linked):
                raise AuditIncomplete("duplicate_child_result")
            for row in child_refs:
                found = by_id[row["child_id"]]
                if found["review_rows"] != 1:
                    child_findings["missing_or_ambiguous_source_review"] += 1
                elif found["linked_children"] != 1:
                    child_findings["missing_or_ambiguous_approval_link"] += 1
                elif not any(dec in ("approved", "approved_held")
                             for dec in found["decisions"]):
                    child_findings["linked_review_not_approved"] += 1
                elif "DONE" not in found["review_statuses"]:
                    child_findings["approved_review_not_terminal"] += 1
                else:
                    child_findings["linked_with_finalized_review"] += 1

        outbox_findings: Counter[str] = Counter()
        entries = []
        for item in outboxes:
            if (item.get("status") != "HELD_UNTIL_ADMISSION"
                or item.get("verified_operator") is not False
                or item.get("signed_admission") is not False):
                outbox_findings["unexpected_or_elevated_outbox"] += 1
            rid, cid = item.get("review_id"), item.get("child_id")
            if not isinstance(rid, str) or not rid.strip() or not isinstance(cid, str) or not cid.strip():
                outbox_findings["invalid_outbox_pointer"] += 1
            else:
                entries.append({"outbox_id":item["id"],"review_id":rid,"child_id":cid})
        if entries:
            linked = _rows(session, _OUTBOX_LINKS, {"entries":entries})
            if len(linked) != len(entries):
                raise AuditIncomplete("ambiguous_outbox_relation_sample")
            for row in linked:
                if row["outbox_links"] != 1 or row["approved_children"] != 1:
                    outbox_findings["incomplete_outbox_links"] += 1

    return {
        "decision":"HOLD",
        "read_only":True,
        "sample_limit_per_type":limit,
        "truncated":truncation,
        "counts":{name:len(rows) for name,rows in sets.items()},
        "review_statuses":_count_status(reviews,"status"),
        "review_decisions":_count_status(reviews,"decision"),
        "child_statuses":_count_status(children,"status"),
        "approved_review_missing_unique_child_link":review_approval_missing,
        "child_pointer_findings":dict(sorted(pointer_errors.items())),
        "child_link_findings":dict(sorted(child_findings.items())),
        "outbox_findings":dict(sorted(outbox_findings.items())),
        "coverage":"bounded_current_graph_only_not_external_custody",
    }
