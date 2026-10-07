# Graphify sidecar adoption

Graphify is a non-authoritative repository-graph extraction source. The fleet pilot pin is graphifyy 0.9.77, installed in an isolated user-local virtual environment.

GraphifyAdapter exposes only local code extraction using graphify extract with --code-only and --no-cluster, with output redirected to a separate artifact directory. It does not expose Graphify install hooks, global graph mutation, semantic document extraction, or Neo4j push commands.

Before extraction the adapter records the repository HEAD and dirty state. A caller can require a clean tree for reproducible evidence. The resulting graph.json is normalized through the existing AssistX repository-graph adapter and written as a preview artifact carrying repository and commit provenance.

Every preview explicitly sets authority to preview-only and neo4j_write_allowed to false. A future Neo4j import must be a separate, explicit gate that validates schema mapping, provenance, freshness, and conflict behavior before any write is allowed.
