# AssistX production Neo4j effective grant audit — October 17, 2026

**Release: HOLD / NO-GO. No grant/role/credential changes.**

## Scope and evidence identity

Fleet Commander on x1-370 executed the standalone,
read-only script scripts/read_only_native_graph_privileges.py, bound to:
- exact container identity: assistx-api
- pinned image ID:
  sha256:309b29b4dea94c45f262e217d3121ab1e272b1a47df98394640e6f3916832638
- environment credentials available **inside** that already-running API
  container; never printed or passed on host Docker process argv.

The fixed Python probe uses the Neo4j driver with READ_ACCESS and *only*
these two non-mutating system-database Cypher statements:

    SHOW USER PRIVILEGES
    SHOW CURRENT USER YIELD roles RETURN roles

No generic query option, direct shell flag, production mutation, privileged
Docker socket mount, or credential export is implemented. Inspection occurs
before and after the immutable container ID/image check. The output excludes
all usernames, graph URIs, passwords, role names, full raw SHOW rows or
credential hashes. An unrecognized result fails closed.

## Actual native findings, redacted

- Native effective privileges granted: **19**
- Effective wildcard graph grants: **18**
- Effective write-action grants: **2**
- Principal has an administrator role: **YES**
- Effective role count: **2**
- No mutation statements executed: **YES**
- Script with --deny-on-broad-privilege returned code **3**, as designed,
  while printing a non-sensitive JSON report with production_decision=HOLD.

The earlier Docker environment equality census also found the API, worker and
Hermes use the same Neo4j principal reference; native effective privileges
above apply to the API's current principal, not separately authenticated
worker/Hermes identities. A DB grant census is not signed provenance.

## Test and safety evidence

- Local scoped audit and supervisor regression: **73 PASS, 0 FAIL**.
- Ruff focused check: PASS. Python source compile: PASS.
- The original production readback succeeded interactively as a fixed
  Neo4j READ_ACCESS session, consistent with this repeatable tool.
- Initial script attempts refused an incorrectly copied image SHA and
  an overly strict Docker inspect output bound. The output bounds were
  separated (larger for Docker metadata, strict for native JSON), tested
  offline and rerun. This was a **tool preflight problem**, not a Neo4j
  database failure.
- x1-370 had independently rebooted at 11:55 EDT; API/router /health
  returned 200 after reboot. The sealed Auto-Router draft PR #71 canary
  was recovered on network=none without modifying the production router.

## Release prerequisites — still P0 open

- Move API/worker/Hermes to independently approved least-privileged
  identities; do not revoke or rotate current production principal until
  canary access/rollback and restricted native direct Bolt denials pass.
- Resolve Context #46 independent, non-restorable operation-specific COMMIT
  and spent high-water custodian. Source and disposable tests still demonstrate
  co-restoration replay and direct database owner bypass.
- Resolve Context #45 GitHub Actions account billing/spending admission before
  exact-head hosted verification.
- Verify signed image build provenance, owner-bound receipts and review all
  active network paths before any automatic failover or merge/deployment.

**This is read-only evidence collection, not permission to deploy.**
