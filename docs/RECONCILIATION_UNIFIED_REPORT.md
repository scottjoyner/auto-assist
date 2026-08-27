# Unified reconciliation report

`scripts/reconciliation-unified-report.py` produces one machine-readable, read-only
artifact from two operator inputs:

- the migration ledger (`deploy/reconciliation/migration-state.yaml`); and
- the candidate-only Tailscale inventory produced by
  `scripts/reconciliation-discover-tailnet.py`.

It records source SHA-256 hashes and summarizes ledger status, reachability
candidates, online candidates, admitted/quarantined runtimes, and blockers. The
embedded `authority` field explicitly states that the artifact does not authorize
production changes.

This is intentionally an artifact generator, not another control plane. It does
not probe endpoints, write Neo4j, claim tasks, admit capacity, route requests, or
start/stop services. Runtime admission remains governed by the existing ledger
validator and operator approval gates.

## Usage

```bash
python scripts/reconciliation-unified-report.py \
  deploy/reconciliation/migration-state.yaml \
  --candidates artifacts/reconciliation-tailnet-candidates.json \
  --output artifacts/reconciliation-unified-report.json
```

Or, after initializing a reconciliation workspace:

```bash
make reconciliation-unified-report
```

The candidate input is optional. When omitted, the report records
`candidate_inventory.authority` as `not_provided` and reports zero candidates;
it never infers reachability from the ledger.
