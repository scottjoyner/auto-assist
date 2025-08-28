
from __future__ import annotations
from .neo4j_client import Neo4jClient
from pathlib import Path
import json

def export_predictions(out_dir: str, limit: int = 100):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    neo = Neo4jClient()
    with neo.driver.session() as s:
        res = s.run("""
            MATCH (c:Conversation)-[:HAS_SUMMARY]->(s:Summary)
            WITH c, s ORDER BY s.created_at DESC
            MATCH (s)-[:GENERATED_TASK]->(t:Task)
            WITH c, s, collect(t) as tasks
            RETURN c, s, tasks LIMIT $limit
        """, {"limit": limit})
        rows = [(dict(r[0]), dict(r[1]), [dict(x) for x in r[2]]) for r in res]
    neo.close()
    for c, s, tasks in rows:
        data = {
            "id": c.get("id", c.get("title")),
            "summary": s.get("text",""),
            "tasks": [{"title": t.get("title"), "priority": t.get("priority"), "due": t.get("due")} for t in tasks]
        }
        name = data["id"] or c.get("title","conversation")
        (out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
