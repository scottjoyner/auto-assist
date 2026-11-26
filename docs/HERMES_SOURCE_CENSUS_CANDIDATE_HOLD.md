# Hermes source-owner census — read-only candidate (HOLD)

This research module is an internal Neo4j candidate reader, not a
production endpoint, admission decision, owner authority or revocation
receipt. It is deliberately not wired into a running service.

It scans source Task records associated with hermes-local, including
active assignments with missing claim IDs, and terminal tasks still
carrying a claim ID. There is no 500-row general-API cap. It takes
two bounded and ordered scans, rejects overflow, duplicate IDs,
missing claim/session IDs, wrong owners and invalid generations.
It returns only a count and domain-separated digest, never task data.

Scope and limitations:

- Max 10,000 Task records, with overflow failing closed.
- Repeated reads detect simple drift, not concurrent ABA changes.
- Neo4j read-committed transactions do not establish a globally
  consistent source generation across all claim writers.
- Tasks without either an Hermes owner/target marker can be missed;
  a real authority needs dispatch provenance and owner-bound grants.
- Externally committed effects and other stores are not observed.
- A digest derived by the worker is not a trusted external head.
- A zero count is NOT proof of quiescence or safety.
- No HTTP route, production graph query or deployment is enabled.
- All results explicitly deny production release and return HOLD.

Evidence:
- 17 synthetic Python tests including 621 rows, missing claims,
  malformed records, bounded overflow, changes and driver errors.
- Four real disposable Neo4j cases: 621-row scan, 600 limit denial,
  missing active claim denial and externally injected claim churn.
  The DB used Docker internal-only network, zero published ports
  and zero production mounts; test tasks never left the disposable DB.

Required before promotion: transactional source generation fencing
across claim/reissue/heartbeat/complete/cancel/task creation, an
owner-authenticated complete source inventory, independent
rollback-resistant custody with monotonic high-water, effect
settlement proof and restart/revocation negative testing.
See auto-assist P0 #427, #428, #426 and #414.
