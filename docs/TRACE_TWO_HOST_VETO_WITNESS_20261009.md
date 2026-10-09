# AssistX #148 — Two-node signed veto witness, not consensus (2026-10-09)

**Status: research draft / NO production authority.** This work stacks on [PR #227](https://github.com/scottjoyner/auto-assist/pull/227). It does not modify production API, Neo4j, Redis, Tailscale, ingress, model routing, actual execution, NAS, or fleet admission.

## Hypothesis

With capacity one, a single primary SQLite admission store on x1-370 can be rolled back or copied. Requiring **a separate nonexpiring xwing witness endorsement** before declaring the research proposal witnessed should prevent a forked/stale x1 primary from obtaining a *second* valid witness response **as long as there is only one surviving xwing signer and state store**. Loss of xwing must veto, never promote a local copy.

A completed *proposal* deliberately **does not authorize physical Neo4j execution or release**. The signer controls only its own research journal and signs a record explicitly marked `witnessed-not-executable`.

## Protocol (all disposable)

- Primary (`src/assistx/trace_two_host_authority_research.py`) holds an occupied slot and creates a unique `token`, `receiver_nonce`, `query_ref`, sequence and pinned UUIDv4 epoch/64-hex graph identity.
- Secondary (`src/assistx/trace_two_host_veto_witness_research.py`) keeps an independent `/tmp/assistx-twohost-witness-test-*/witness-test.sqlite` with a nonexpiring witnessed slot and high-water sequence. It refuses missing or stale sequencing, concurrent second slots, wrong epoch/graph/signer digest, dangerous paths, replaced files, unreadable signing key, or untrusted preregistration.
- Secondary generated a throwaway Ed25519 key in a mode-0600 research fixture on **xwing**. Enrollment returned public key and fingerprint **before any admission request**. Primary copied only the public key via existing SSH/SCP and pinned the digest; a subsequent SSH call requested the signature. A response never carries a public key the primary can accept as its own trust root.
- Endorsement happens *after* the x1 slot is reserved, and xwing's evidence is signed *after* its separate local SQLite write is durable (`BEGIN IMMEDIATE`, `synchronous=FULL`). A crash between those events leaves capacity occupied and uncertain, not eligible for automatic repair.
- The primary research round-trip `tests/probe_trace_two_host_veto_roundtrip.py` does not dispatch work. It returns `execution_authorized=false` and `slot_released=false`, even with a verified signature. Missing witness leaves the primary slot occupied.

## Physical execution: x1-370 and xwing

Research epoch: `83f2b760-af25-4f94-87a2-7a3a5918bcf4`, graph identity exactly 64 lowercase `a` characters. The only transport was the already-present authenticated SSH service. No host service, firewall or production credentials were adjusted. Files were isolated to `/tmp/assistx-twohost-authority-test-20261009-veto*` (x1), `/tmp/assistx-twohost-witness-test-20261009-physical` (xwing), and `/tmp/assistx-twohost-veto-pin-20261009-physical.pub` (x1).

1. Enroll xwing throwaway public key before request: independently checked SHA-256 **`b6e79a43652f4dcb04d819cf6113de6ecb6a4863e30ac1fd654d3d910ae2d546`**. Only the public key crossed hosts, not the private key.
2. Reserve capacity=1 in x1; xwing persisted sequence 1 and signed same epoch/graph/token/nonce/query/sequence. x1 verified the signature against **pre-enrolled public key**, returned `proposal-dual-witnessed-research-only`, active=1, `execution_authorized=false`, `slot_released=false`.
3. Bootstrap a separate, fresh **x1 primary copy** with the same epoch and seq=0. Its new sequence-1 reservation could **not** obtain a second xwing endorsement: `witness-unavailable-conservative-hold`, active=1, no signature or execution.
4. Bootstrap another fresh x1 primary fixture; intentionally interrupt xwing SSH via `ProxyCommand=/bin/false`. Again `witness-unavailable-conservative-hold`, active=1, no execution. This is **SSH delivery failure**, not a real production partition.
5. Read-only check on xwing confirmed witness `highwater=1`, `occupied=1`, same enrolled signer fingerprint. No automatic slot releases were performed.

## Source-owned tests

- Native x1-370: **38/38** tests passed for new veto-witness, existing two-host research authority, and SSH client.
- Native xwing: **18/18** new witness tests passed on same Git branch.
- The tests require a valid pre-enrolled signer digest, deny substituted graph/epoch/token/nonce/sequence/query, reject missing or replaced witness state, verify 1/3/5/10 concurrent single-host proposal waves, survive restart without duplicate acceptance and refuse new endorsement when occupied.
- **Expected unsafe counterexamples are intentionally passing tests:** copying *both* xwing's private key and its empty witness journal allows a second independent witness to sign a conflicting sequence-1 grant; restoring the witness journal to an empty copy and restarting with a **stale** high-water checkpoint can sign again. A genuinely independent sequence floor detects the rollback. These remain HARD BLOCKERS, not resolved by passing tests.
- Hosted GitHub CI is configured for synthetic tests only and is **not** physical SSH acceptance.

## Important trust limitation

The experiment runs under a general-purpose SSH account on both hosts. **The x1 caller can potentially log in to xwing with the same account and read the throwaway private key.** The private key was not *transmitted*, but xwing is **NOT an independent security principal** with hardware/KMS-backed custody or restricted signed-only SSH command access. Enrollment via SSH host key is an early pin, **NOT operator/PKI provisioning independent of the account**. Do not promote this to cryptographic quorum authority.

There are only two nodes and no consensus/majority protocol. Under dual-copy rollback or copied signer key, both forks can sign. A witness outage safely vetoes at the expense of availability and stranded reservations. There is no automatic failover, durable externally anchored epoch floor, nonrollbackable global consumed-receipt authority, or physical query termination truth.

## Remaining #148 / #149 acceptance

1. Operator-owned, separately protected receiver/witness signing keys; a pinned trust root distributed through independent provisioning, attested identity and rotation/revocation. Never accept trust anchors delivered as request data.
2. True durable distributed consensus/lease fencing with one surviving authority, no automatic replica promotion under partition, and a monotonic epoch and consumed-receipt store immune to replay/rollback across restart.
3. Authenticated x1/xwing multiworker physical Neo4j reads with real outage/failover, latency bounds, positive and negative security controls. These tests issue **research reservations only**, not real graph work.
4. Physical ingress #149 Caddy/Tailscale provenance, graph isolation, per-role PII access, manual browser/device and operator rollback evidence.

**Disposition: research veto is reproducible and conservative; physically distributed admission safety remains unproven. Keep #148 OPEN, this PR DRAFT, runtime admission disabled.**
