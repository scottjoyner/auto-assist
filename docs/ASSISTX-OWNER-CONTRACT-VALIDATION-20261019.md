# AssistX owner-contract validation — October 19, 2026

**Research-only. Default-off flags; no production migration or routing.**

Follow-up to AssistX draft PR #413 (producer) and Auto-Router draft
PR #72 (consumer).

## Native disposable Neo4j concurrency — PASS

Reproduce explicitly with:
  python3 scripts/verify_owner_neo4j_disposable.py --run

The script used a preexisting local neo4j:5.23.0 community image with
SHA-256 image identity:
sha256:1c7b2197f0fcb5d9ed6456804121667262b3cd0cee8f9e6889d17e265c0b3cb9

Environment: x1-370 disposable Docker container, Docker network none,
no published host ports, no production DB URI/credentials, no mounted
volumes. Caps: 2 CPUs, 3 GiB container memory, 192 PIDs and restricted
privileges. Bounded Java heaps and two simultaneous Cypher-shell clients.
The disposable container was automatically removed and its absence
independently confirmed via docker ps -a.

An empty native Neo4j database verified the owner constraint absent,
then installed the exact REVIEW-ONLY Cypher migration inside its
ephemeral filesystem. It confirmed the Task uniqueness constraint's
name, UNIQUENESS type, Task label and owner_idempotency_key property.

**Nine native Cypher writes**, with two database clients executing
concurrently, returned exactly **two tasks and two owner-scoped keys**:
six same-key retries for synthetic owner A collapsed to one Task;
three same-key retries for synthetic owner B collapsed to a second.
Owner A's stored subject remained unchanged by retries.
Final result PASS; physical run duration approximately 28.9 seconds.

The native test uses the same CREATE-only MERGE and uniqueness
constraint as the proposed writer but invokes Cypher-shell rather than
the Python Neo4j driver. This is native schema/concurrency evidence, NOT
a verified application driver transaction, multiprocess cluster,
graph-wide owner immutability, or reliable notification outbox test.

Initial harness failures were environment-only: an unresolvable
container hostname under network isolation, then excessive memory
from multiple Cypher-shell JVMs. A fixed localhost hostname and two
bounded JVM clients passed without allowing network access. No live
fleet or production graph was affected.

## Signed AssistX/Auto-Router owner contract — PASS

New tests/test_owner_signed_router_contract.py exercises real
ephemeral Ed25519 JWT signatures through AssistX's TestClient token
issuer and claim-status APIs using a fake isolated Task authority.
Two synthetic principals alpha/beta receive distinct signed JWTs.
The optional peer source is loaded from AUTO_ROUTER_OWNER_FENCE_PATH,
without invoking the live Auto-Router or real model endpoints.

Eight tests passed: two owner JWTs and matching status; cross-owner
AssistX claim-status denial; revoked claim denial; tampered signature
denial; ownerless legacy Task refusal; router gateway-owner mismatch
denial; router claim-status owner switch denial; and matched owner
acceptance. Test principals and Tasks are synthetic, NOT real
authenticated human users or production execution receipts.

The combined focused suite (prior 29 + new eight) passed **37/37**.
One FastAPI/Starlette client deprecation warning is unrelated.

Reproduce, from the AssistX checkout:
  PYTHONPATH=src AUTO_ROUTER_OWNER_FENCE_PATH=/path/to/auto-router/src/auto_router/owner_binding.py python3 -m pytest tests/test_owner_authority_contract.py tests/test_owner_signed_router_contract.py tests/test_executor_claim_status.py tests/test_executor_claims.py -q

Without the optional peer path, Auto-Router contract tests skip;
the AssistX JWT and claim-status tests still run.

## Outstanding production gates

- Real independent multi-user identity gateway and trusted header
  provenance. Do not derive an owner from request JSON or agent/task ID.
- Apply/migrate only after approved staged Neo4j concurrency/rollback,
  full Task mutation review, source custody, and native Python driver
  acceptance, plus a durable task-notification outbox.
- Persistent signed per-owner execution receipts, revocation and
  cross-worker/restart replay-high-water protection.
- Explicit task-scoped Lenovo capability fallback; verified model
  admission, direct listener restrictions, fault injection and 72h soak.

Both owner flags remain DISABLED. No live schema, real user credentials,
inference routing, or production services were changed. GLM production
and automatic Lenovo fallback remain HOLD.
