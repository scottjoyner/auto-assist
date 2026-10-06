import sys; sys.path.insert(0, '/app/src')
from neo4j_client import Neo4jClient
import assistx.control_room as cr

f = lambda: Neo4jClient()
hist = cr._historical_loaded_models(f)
print("=== _historical_loaded_models (container) ===")
for node in sorted(hist):
    h = hist[node]
    print(f"  {node}: age_ms={h['age_ms']}, models={[m[:45] for m in h['models']][:2]}")
