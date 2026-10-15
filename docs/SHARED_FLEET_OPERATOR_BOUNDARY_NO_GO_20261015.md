# Fleet administrative custody: read-only cross-host NO-GO

**October 15, 2026 | x1-370 | Research-only | No production GO.**

## Actual fleet witness (no writes)

A read-only inventory from Fleet Commander confirmed xwing and destroyer
were online. SSH via existing aliases in BatchMode, StrictHostKeyChecking,
and a fixed read-only command returned the same operator's control
reachability on each node. Docker client access was available under that
SSH identity in both cases.

The new deny-only audit returned:

    NO_GO_SHARED_OPERATOR_DOCKER_REACHABILITY
    xwing: operator_reachable=true, docker_admin_accessible=true
    destroyer: operator_reachable=true, docker_admin_accessible=true

The audit did not inspect any secrets, IP addresses, SSH private keys, token
values, Docker workloads, privileged files or production databases. It did
not start containers, change network policy, SSH configuration, daemon ACLs,
credential custody or services. It reported only sanitized aliases and
reachability booleans, not remote usernames.

This is evidence that **the current operator can administer Docker on both
nodes**, *not* a statement that the same OS UID is cryptographically the same
principal across hosts and not proof of a security compromise. It defeats
the proposed claim that moving the resource to xwing and anti-restore
high-water to destroyer would automatically provide independent custody.

## Reproduce without fleet writes

    python3 scripts/audit-disposable-fleet-admin-boundary.py xwing destroyer

Requirements: existing SSH aliases, host keys pinned in known_hosts, and
only read-only Docker client introspection on the selected fleet nodes.
The tool requires explicit, strictly validated SSH aliases and never
interpolates aliases into a remote shell statement. SSH failures, malformed
or missing remote self-reports, absent Docker access, and partial or
ambiguous observations yield UNKNOWN, never PASS. Even a physical shared
operator witness exits nonzero with a NO_GO verdict. No release pipeline
automatically invokes the physical audit.

Unit tests are synthetic: they pass a fake subprocess runner and never
open SSH, Docker, files or network connections:

    python3 -m pytest -q tests/test_disposable_fleet_admin_boundary.py

## Exact trust implications

Multiple PostgreSQL clusters under a single Docker host were already shown
in draft #377 to be vulnerable to the host Docker administrator restoring
the high-water store. Moving two *disposable* clusters to different hosts
might improve availability and failure-domain diversity. It would **not**
prove custody separation when an actor has remote Docker administration on
both. The present probe only establishes overlapping operator access, not
durable ACL immutability, cryptographic trust roots, independent snapshot
backup policies or non-colluding organizational administrators.

Even an unreachable second host cannot be certified independently
governed: unreachable means **UNKNOWN**, not secure. This tool is
intentionally **deny-only** and provides no GO path.

## Required next approval outside this research branch

An independently controlled administrative domain (separate host
administrator, secret custodian, controlled trusted signer, independent
backup/restore approval) must be designated. Test that the resource
administrator **cannot** reset or create high-water records, including
through the underlying host/orchestrator, snapshots or emergency accounts.
Verify read-only durable resource-side COMMIT provenance and term/sequence
fencing after uncertain COMMIT. Independently witness all normal and
adversarial cases with authorization, and do not permit automatic replay,
restart, merge or deployment from any research PASS.

Outstanding authority/release holds: Auto-Assist #156/#299, Kipnerter API
#46/#31/#45, Auto-Router owner-bound execution receipts, iOS device
acceptance and all unmerged drafts #368–#377.

**No branch merge, production graph, credential, networking, model, failover,
Apple/iPhone or deployment changes are authorized.**
