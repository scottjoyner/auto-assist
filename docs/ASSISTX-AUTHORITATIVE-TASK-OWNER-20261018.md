# AssistX authoritative owner contract — research only

October 18, 2026. This is a **default-OFF**, fail-closed producer slice
for Auto-Router's draft owner fence (PR #72). No live Neo4j migration,
token issuance changes, gateway middleware deployment or route changes
were performed. GLM and Lenovo automatic fallback remain HOLD.

## Observed gap

Today, /api/tasks authenticates a caller but does not persist the
caller identity as the Task's canonical owner. Legacy upsert_ticket()
idempotency merges use a global key and SET t += props, allowing later
retries to modify fields of an existing Task without owner checks.
The executor token and claim-status responses bind the claim, agent and
projection, not separately authenticated human owners.

## Isolated opt-in source change

ASSISTX_REQUIRE_OWNER_BINDING=false (default):
- Existing Task create, executor issuer and status behavior remains
  backward compatible. No legacy Task is migrated or implicitly owned.
- A Task with valid stored canonical owner fields may have them included
  in newly issued signed token claims and live status, but legacy tasks
  without owners remain unowned.

ASSISTX_REQUIRE_OWNER_BINDING=true (future explicit test canary):
- Task creation requires separately authenticated gateway scope state:
  verified_owner with authenticated=True, owner_issuer and owner_subject.
  The subject must match the authenticated task submitter. Header and
  task-payload owner claims are not trusted identity sources.
- Creation uses an owner-scoped SHA256 key and a *separate create-only*
  MERGE writer. A retry returns the same Task without rewriting it,
  including a Task that has since been claimed. Two distinct owners
  using the same client retry key obtain distinct task identities.
- Writer first validates that the exact Neo4j Task uniqueness constraint
  is installed with the expected type, label and property.
  Without it, no Task write occurs. Database uniqueness is essential
  for simultaneous retry safety on multiple API instances.
- Issuer fetches the canonical owner from stored Task properties, not
  mutable payload or request headers, and includes it in the signed JWT.
  Ownerless/malformed Task records are rejected for strict issuance.
- Both claim-status implementations include canonical stored owners.
  Missing/malformed owner results in inactive claim status in strict
  mode. The resource-side claim verifier also compares token owner to
  live Task owner to reject owner switches.
- Strict mode with the current live gateway would correctly deny
  ownerless requests. DO NOT turn it on until the verified principal
  middleware and identity provider are independently deployed and
  accepted.

## Schema handoff — NOT applied

Review migration:
  migrations/20261018_task_owner_unique.cypher

Only an operator-approved deployment should create the uniqueness
constraint, after verifying Neo4j version, write locks, current schema,
backups and any conflicting index/constraint. This change DOES NOT run
the migration. Do not remove the constraint blindly during rollback
if owned tasks have been written.

## Validation and limits

29 focused deterministic tests passed across owner authority, legacy
executor claims and claim-status. Fake Neo4j sessions prove no
cross-owner retry alias, no writes when required schema is absent or
wrong, no update on retry, malformed/forged principal rejection,
and strict claim-status rejection of ownerless Tasks.
No native Neo4j transaction, multiserver concurrency, migration,
real multi-user gateway, signing custody or live inference test was run.
Two existing async executor tests could not collect in the available
Python 3.12 pytest environment (missing pytest-asyncio). An alternate
venv had missing project dependencies; report this as unverified.

Important unresolved concerns before production:
1. Verify TRUSTED_AUTH_HEADER stripping at the gateway and introduce
   a real authenticated multi-user principal source. The current
   Basic account does not prove two independently authenticated users.
2. Extend verification to ALL Task mutation paths and preserve owner
   immutability throughout migrations and administrative operations.
   A create-only writer by itself is not a graph-wide tamper lock.
3. Demonstrate signed issuer and resource-side owner receipt exchange
   with both distinct real test principals, independent source custody,
   stale/forged/revoked negative controls, and actual router PR #72.
4. Persist retry attempts and per-owner terminal receipts across
   router worker restarts. Implement durable outbox delivery for new
   Task notifications and recovery from interrupted notifications.
5. Keep task capability selection explicit: Lenovo 1.2B CPU is not
   automatically equivalent to GLM 321B. Do not deploy fallback until
   loaded-generation, admission, network ACL, rollback and 72h soak pass.

Rollback of source-only test: leave feature OFF and revert review
branch. Production model loading, Tailnet and routing remain unchanged.
