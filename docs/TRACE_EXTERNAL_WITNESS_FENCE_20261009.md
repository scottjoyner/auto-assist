# AssistX #148 — second-host signed witness pregrant and fail-closed replay research

**Date:** 2026-10-09. **Status: DRAFT / RESEARCH ONLY / PRODUCTION NO-GO.**
This stacks on [PR #227](https://github.com/scottjoyner/auto-assist/pull/227), whose exact-head full CI was **1,030 passed, 59 deselected, recovery-canary PASS**. It does not modify runtime admission, Neo4j, Redis, Caddy, Tailscale, or connected services.

## Intent

A host holding a local SQLite admission DB is not a safe replacement authority. Prior physical x1-370/xwing experiments proved that **two independent database copies each admit work even when they share the same epoch**. This slice adds a *second, independently stored, non-expiring witness* on xwing, required by a new research-only x1-370 two-step admission CLI before that CLI can create a local grant.

The new witness is **not** a quorum, global consensus authority, trusted production identity provider, failover controller, or physical Neo4j transaction monitor. It does not issue any release operations.

## Research implementation

- `src/assistx/trace_two_host_witness_research.py`: strict, previously bootstrapped `/tmp/assistx-twohost-witness-test-*/` fixture with 0600 Ed25519 private key and public key and an independently stored SQLite ledger; `BEGIN IMMEDIATE`, `synchronous=FULL`, exact epoch/graph, secure file identities and mode checks. A single occupied slot is **never reaped or released**. The receiver's signing key never reaches x1 as private material.
- `tests/probe_trace_witness_fence_cli.py`: research-only CLI, with explicit host and opt-in, preexisting authenticated SSH + known host key. Requests to xwing return a signed grant. The x1 CLI verifies the *pre-pinned* key and the exact signed epoch/graph/query, then may call an isolated research primary transaction. Missing SSH/witness, invalid signature or mismatched fields denies before primary open; no local fallback. A witness can become stranded if the primary fails after witness commit.
- **Identity refinement:** `apply_witness_grant_to_primary` now atomically stores the **same witness-issued reservation token, receiver nonce, sequence and query reference** in x1's local primary DB. It refuses wrong signatures, a divergent sequence or a full capacity. This latest exact-binding refinement is demonstrated by offline tests, not yet by a fresh two-host physical run.
- Only a single lab fixture of capacity one; no direct runtime integration. The legacy `SingleAuthorityResearch.admit` method **remains directly callable without witness**, so the system is **not actually fenced**. The intentionally passing bypass test documents this blocker.

## Physical experiment evidence, with exact limits

On the **first prototype revision** (before the exact-token binding refinement):

1. xwing independently checked out the research source; **13/13 local negative/contract checks passed on each physical node**.
2. On xwing, bootstrap created a **synthetic signing key and journal** under `/tmp/assistx-twohost-witness-test-20261009-live`, and returned a public key to pin on x1. x1 created a separate disposable primary journal under `/tmp/assistx-twohost-authority-test-20261009-witness-live`.
3. The x1 CLI queried xwing over the existing SSH connection, verified the signed witness grant and issued local primary sequence **1**. The response recorded `witness_signature_verified=true`, `primary_called=true`, and `physical_query_executed=false`. Neither node executed a Neo4j query.
4. The first CLI version minted a **separate primary token**, which was a linkage gap. The subsequent source change and tests enforce exact shared signed token/nonce/sequence, but **that corrected revision has not yet been physically rerun against both nodes**. Do not conflate local test evidence with physical acceptance.

No production keys, credentials, graph, model/provider, NAS, or proxy content was touched. The lab generated only disposable temporary data. The new `/tmp` fixtures are subject to explicit cleanup verification; their presence must not be treated as a live fleet authority.

## Native test evidence

`PYTHONPATH=src python3 -m pytest -q tests/test_trace_two_host_witness_research.py tests/test_trace_two_host_authority_research.py tests/test_trace_two_host_client_research.py tests/test_trace_receiver_replay_custody_research.py tests/test_trace_receiver_evidence_research.py tests/test_trace_durable_ledger_research.py tests/test_trace_physical_probe_guardrails.py`

**135 passed on x1-370** after the exact-binding refinement and source-owned witness-bypass counterexample. The witness-only suite is **18 passed**. GitHub exact-head research and full repository CI are separate acceptance gates; they run offline tests, **not physical SSH/Neo4j experiments**.

## Expected counterexamples — why #148 stays OPEN

1. **Copied signing witness:** two independent copies of the witness DB and private key each reserve capacity one and sign distinct grants with matching epoch/graph and sequence. Local SQLite and signatures do not create consensus.
2. **Witness rollback:** copying an older DB back into the same inode, paired with a stale external sequence floor of zero, permits the reinitialized witness to issue another grant. An independently current high-water floor detects that tested rollback, but client-provided floors are not authoritative.
3. **Primary rollback and signed grant replay:** a previous signed witness grant can be replayed against an earlier primary DB snapshot if the *primary* also lacks a current independently pinned checkpoint. The witness may already be full, yet an offline verifier can still validate the old signature. The two-step CLI normally calls xwing online, but the standalone apply method cannot prove online consumption.
4. **Direct-owner bypass:** the research primary's older `admit` method can admit without the witness. Production architecture must have **one mediated admission entry point**; a research client wrapper is not enforcement.
5. **Partial two-step commit:** xwing witness may reserve permanently while x1 fails, intentionally reducing liveness rather than admitting duplicates. There is no recovery/release protocol.

## Next engineering and physical acceptance

- Re-run corrected exact-token implementation physically on x1/xwing with distinct, freshly bootstrapped disposable fixtures; test xwing transport refusal, x1 rollback while witness remains full and direct-owner bypass as a negative. Record only bounded synthetic metadata. Clean all remaining temporary state and credentials.
- Remove direct-owner bypass in a separately authorized, server-mediated admission implementation. Make witness confirmation and consumption **online and one-time**, not a replayable detached signature. Neither untrusted workers nor a copied owner may promote itself.
- Build a **separately protected monotonic epoch/sequence authority** (quorum/consensus or independently owned fencing service) that survives x1/xwing partitions and rollback, with operator-pinned Ed25519 trust, revocation and durable consumed-nonce records.
- Integrate with **real physical Neo4j query transaction IDs**, independently attested termination, and authenticated multi-host 1/3/5/10 staged traffic; connect to the already-proven research blackhole tests only when the physical negative cases pass.
- Independently resolve #149 Caddy/Tailscale trusted ingress spoofing, role authorization and redaction. No production deploy, main merge, auto-release or failover promotion until then.

**Disposition: narrower failure mode is covered, source-tested; fleet-wide fencing and real query admission remain unproven. KEEP #148 OPEN.**
