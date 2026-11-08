# Host-local fenced model-slot ledger — synthetic and physical staging

**November 7, 2026. Research/source-only. Production Task admission: HOLD.**

The current fleet's WorkSupply controller provides a useful control-plane
readiness signal, and the merged effect-time Task guard (#576) checks the
canonical owner's grant and WorkSupply heartbeat. Neither check prevents two
independent processes from trying to occupy the **same physical inference
runtime**. This source adds an atomic host-side slot ledger as one building
block for a future separate, credential-custodied model gateway.

## Scope and boundaries

`src/assistx/fleet_resource_slot_ledger.py` is a **Python standard-library,
SQLite `BEGIN IMMEDIATE` transactional slot allocator**. It cannot connect
to Neo4j, discover runtime/model capacity, issue runtime grants, inspect
private keys, or call a model. It NEVER provisions a grant database. A caller
must supply a pre-existing host-local SQLite ledger in a private directory
owned by the **same dedicated gateway principal**, inaccessible to other
users; SQLite is opened in `mode=rw`, never auto-created, and symlinks,
hardlinks, world/group access or unsafe immediate parents are denied.

A separately authorized administrator/provisioner must create the exact
`resource_grant` and `resource_slot` tables and verify a fresh independent
owner-signed resource manifest/required-node roster. The ledger matches
an already-established runtime ID, generation, exact approved capacity and
64-hex owner receipt digest. Merely placing a SHA-256 string in SQLite does
**not** establish a valid owner signature; the signer and provisioning
boundary remain separate responsibilities. Normal Task clients must NEVER
be able to open or mutate this SQLite database or call the upstream model
directly. The repository includes no production database-provisioning
entrypoint, service enablement or deployment instruction.

Under a SQLite cross-process write lock, the ledger checks the exact grant,
valid time, active status, capacity and slot layout on **every transaction**.
It persists an increasing wall-clock high-water mark to reject time rollback
large enough to resurrect expired leases. A request may acquire only one
slot per active Task (even with a different claim ID); no capacity >16 or
lease >30 seconds is allowed. Each slot has an ever-increasing per-slot
fencing epoch, cryptographically random token (SHA-256 only stored at rest),
exact Task and claim identity, and short expiration. Acquire, validate,
renew and release are conditional on that exact grant, identity, fencing
epoch, token and current lease version. A replayed release after reassignment,
a forged token, expired lease, old generation or stale pre-renewal lease
cannot free or use a successor's slot.

**Important limitation:** `validate_effect` is a *momentary transactional
resource-side check*, not an atomic barrier surrounding a model invocation
or a distributed Neo4j Task COMMIT. An independent model-host sidecar must
own **and exclusively mediate** upstream model sockets/processes, acquire
and hold a slot while forwarding a bounded inference request, cancel on
lease loss, and perform resource-owned acceptance/commit with generation
fencing. Direct host-loopback LM Studio/llama-server access remains possible
in the present fleet, so the ledger does **not** prove physical inference
exclusivity or prevent bypass by other host processes. Never deploy the
ledger as the sole admission check, especially not inside the API container.

## Real, bounded host LFM3 witness on x1-370

The `scripts/fleet_resource_slot_local_staging_witness.py` program defaults
to **exit 2/HOLD, with no network/model/database effects**. A deliberate
operator-only `--live` run creates a **disposable test-owned** `0700`
temporary directory and `0600` SQLite test fixture, with a *synthetic*
generation 897 and synthetic all-zero owner marker (not a real lease, approval
or credential). No production data or Graph writes occur. It confirms the
existing `127.0.0.1:19181` LFM3-1.7B model ID and health, acquires one
synthetic slot, verifies a competing claimant cannot get that slot, sends
**one fixed 4-token-max** benign prompt to host-local LFM3, checks the
lease and response budget, releases the slot, proves replayed release denial
and disposes of the entire private temporary directory. It always exits
**2/HOLD** even when the physical staging proof passes; no positive result
can be mistaken for production release authorization.

On November 7 two successive bounded physical runs on the unchanged
resident LFM3 model each returned 4 completion tokens. The latest run
completed in approximately **136 ms**; the competing slot was denied;
token replay was denied; the ephemeral DB was deleted, original LFM3 health
was intact and live machine Task GET remained 403. This is a **staging
transport and coordination proof only**. It does not establish complete
resource ownership, approved inference concurrency, a claim/COMMIT workflow,
or owner-signed generation 897.

## Tests and safe release sequence

`tests/test_fleet_resource_slot_ledger.py` uses synthetic private SQLite
files and independent competing threads and OS processes to validate atomic
capacity, cross-process contention, lease/renew/release replay, expiry,
owner-generation swap, duplicate Task claim, clock rollback, corrupted or
insecure file, lease-token tampering, old token after slot reuse and no
self-provisioning. CI runs **synthetic tests only**, without `--live`, service
identity, model requests, signer or fleet network access. The full existing
Task/claim/projection test suite is separately required before source merge.

**Further release gates** before any production model calls through this
ledger:

1. An independent owner-managed Ed25519 verification root and fresh owner-
   signed runtime/model/access/capacity manifest and required-node roster
   (canonical generation 896 was expired; the root trust key is absent).
2. A separate least-privilege **host-local gateway user/process** that
   exclusively owns all upstream inference paths, a trusted provisioner that
   validates the detached receipt and atomically creates/seals the grant DB,
   an authenticated Docker-private caller and bounded model request schema.
3. Inference-time resource ownership, concurrent slot renewal/cancel/fence,
   replay and crash/restart handling, gateway SLO/metrics, private service
   credential rotation and reviewable rollback with no public ingress.
4. **Resource-owned Task commit** tied to exact Task claim/generation/slot
   lease, with independent admission check and negative cross-node and
   expired-credential tests; then one supervised staging Task canary.

The production API and Neo4j were observed healthy, protected production
checkout stayed pinned to the old release, WorkSupply remained zero approved
slots and the intentionally disabled October projection keepalive remained
inactive. No one has enabled Task admission, opened firewall ports, created
production signing keys, or deployed this source. See auto-assist #284,
#569 and #501.
