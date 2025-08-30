from typing import List, Dict, Any, Tuple
from ..neo4j_client import Neo4jClient
from .engineer import draft_cypher, repair_cypher

def execute_with_repairs(
    neo: Neo4jClient,
    question: str,
    schema: Dict[str, Any],
    max_attempts: int = 3,
    log_cb=None,
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns: (final_cypher, rows, attempts_log)
    attempts_log: [{"cypher":"...", "ok":bool, "error":str|None, "fix":str|None}]
    """
    attempts = []
    plan = draft_cypher(question, schema)
    cypher = plan.get("cypher", "")

    for attempt in range(max_attempts):
        try:
            if log_cb: log_cb(event="try_cypher", payload={"cypher": cypher, "attempt": attempt})
            with neo.driver.session() as s:
                res = s.run(cypher)
                rows = [dict(r.data()) for r in res]
            attempts.append({"cypher": cypher, "ok": True, "error": None, "fix": plan.get("notes")})
            return cypher, rows, attempts
        except Exception as e:
            err = str(e)
            attempts.append({"cypher": cypher, "ok": False, "error": err, "fix": None})
            if attempt >= max_attempts - 1:
                raise
            plan2 = repair_cypher(cypher, err, schema, question)
            cypher = plan2.get("cypher", cypher)
            if log_cb: log_cb(event="repair", payload={"from": attempts[-1]["cypher"], "to": cypher, "reason": plan2.get("fix")})
            plan = plan2
