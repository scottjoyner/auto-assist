#!/usr/bin/env python3
"""Export my-jev shadow evidence from AssistX Neo4j as local JSONL.

This is intentionally an operator-run read-only exporter. It does not redact
utterance/conversation text. Keep raw exports local until a separate redaction
step has been applied.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.neo4j_client import Neo4jClient


def export_rows(
    neo: Neo4jClient,
    *,
    limit: int = 0,
    since_ts: int = 0,
) -> list[dict[str, Any]]:
    query = """
    MATCH (i:Intent)
    WHERE i.policy_shadow_json IS NOT NULL
      AND coalesce(i.policy_shadow_at_ts, i.created_at_ts, 0) >= $since_ts
    WITH i
    ORDER BY coalesce(i.policy_shadow_at_ts, i.created_at_ts, 0) ASC
    WITH i
    LIMIT CASE WHEN $limit <= 0 THEN 2147483647 ELSE $limit END
    RETURN
      i.id AS intent_id,
      i.source AS source,
      i.text AS text,
      i.classification AS legacy_classification,
      i.policy_action AS legacy_policy_action,
      i.policy_shadow_json AS policy_shadow_json,
      i.policy_shadow_contract AS policy_shadow_contract,
      i.policy_shadow_route AS policy_shadow_route,
      i.policy_shadow_disposition AS policy_shadow_disposition,
      i.policy_shadow_policy_action AS policy_shadow_policy_action,
      i.created_at_ts AS created_at_ts,
      i.policy_shadow_at_ts AS policy_shadow_at_ts,
      [(i)-[:CREATED_TASK]->(t:Task) |
        t{
          .id,
          .title,
          .kind,
          .status,
          .priority,
          .claimed_by,
          .result_summary,
          .created_at_ts,
          .completed_at_ts
        }
      ] AS created_tasks
    """

    with neo._session() as session:
        records = session.run(
            query,
            {
                "limit": int(limit),
                "since_ts": int(since_ts),
            },
        )
        rows = [
            dict(record)
            for record in records
        ]

    for row in rows:
        # The raw export has not passed through a redaction pipeline.
        row["redacted"] = False
        metadata = row.get("created_tasks")
        if not isinstance(metadata, list):
            row["created_tasks"] = []
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export AssistX my-jev policy shadow evidence "
            "to local JSONL"
        )
    )
    parser.add_argument(
        "--output",
        required=True,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="0 means no explicit row limit",
    )
    parser.add_argument(
        "--since-ts",
        type=int,
        default=0,
        help="Only include shadow rows at/after this epoch-millisecond timestamp",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    neo = Neo4jClient()
    try:
        rows = export_rows(
            neo,
            limit=args.limit,
            since_ts=args.since_ts,
        )
    finally:
        neo.close()

    with output.open(
        "w",
        encoding="utf-8",
    ) as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

    print(
        json.dumps(
            {
                "output": str(output),
                "records": len(rows),
                "redacted": False,
            },
            indent=2,
        )
    )
    if rows:
        print(
            "WARNING: export contains raw user/session text; "
            "keep it local until redacted."
        )


if __name__ == "__main__":
    main()
