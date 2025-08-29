from typing import Dict, Any, List
from .neo4j_client import Neo4jClient

def fetch_schema(neo: Neo4jClient) -> Dict[str, Any]:
    """
    Returns a compact schema model:
      { "nodes": [{"label":"Conversation","props":["id","title",...]}],
        "rels": [{"type":"HAS_UTTERANCE","from":["Conversation"],"to":["Utterance"]}] }
    """
    nodes, rels = [], []

    with neo.driver.session() as s:
        # Node labels and sample keys
        labels = [r["label"] for r in s.run("CALL db.labels() YIELD label RETURN label")]
        for lb in labels:
            props = []
            res = s.run(f"MATCH (n:`{lb}`) WITH n LIMIT 50 RETURN keys(n) AS ks")
            seen = set()
            for row in res:
                for k in row["ks"]:
                    if k not in seen:
                        seen.add(k)
            nodes.append({"label": lb, "props": sorted(seen)})

        # Relationship types and sample endpoints
        rtypes = [r["relationshipType"] for r in s.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")]
        for rt in rtypes:
            row = s.run(
                f"""
                MATCH (a)-[r:`{rt}`]->(b)
                WITH labels(a) AS la, labels(b) AS lb LIMIT 100
                RETURN collect(DISTINCT la) AS froms, collect(DISTINCT lb) AS tos
                """
            ).single()
            froms = sorted({tuple(x) for x in (row["froms"] or [])})
            tos   = sorted({tuple(x) for x in (row["tos"] or [])})
            rels.append({"type": rt, "from": ["/".join(x) for x in froms], "to": ["/".join(x) for x in tos]})

    return {"nodes": nodes, "rels": rels}
