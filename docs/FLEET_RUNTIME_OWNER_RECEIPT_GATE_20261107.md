# Canonical runtime approval: independent owner-signed grant (HOLD)

**2026-11-07:** The historical runtime approval CLI `scripts/approve-runtime-projection.py`
accepted `approved_by` and `approval_id` as **unverified strings** in an
operator-authored YAML manifest. Its former `--apply` route could therefore
write `RuntimeInstance.admitted=true`, `AccessPath.approved=true` and
`CapacityObservation.approved=true` with no separate owner cryptographic
approval, so long as the operator had Neo4j credentials and a matching roster.
This is **not** equivalent to the authenticated Ed25519 API *output*
projection: a runtime API signing key proves the response came from the
service, not that an independent owner authorized its input graph grant.

The new source patch adds an **independent, no-self-issue owner approval
gate** to this write-capable publisher. This is intentionally **fail closed**:

- Structural `DRY_RUN_PASS` remains **non-admitting**. It does not indicate
  owner authorization, physical capacity or a readiness decision.
- `--apply` requires the existing independently supplied `--required-nodes-file`
  *and* a new `--owner-approval-receipt`. Omission, unreadable or stale receipt,
  malformed field, wrong manifest hash, wrong generation, operator mismatch,
  wrong cryptographic key or absent trusted owner verification key all stop
  **before opening the database**.
- The **only** trusted Ed25519 owner *public* key location is a
  root-provisioned, non-symlinked file:
  `/etc/assistx/trust/runtime-approval-owner.pub`. All ancestors and this
  file must be **UID 0-owned and not group/world writable**. The script has
  no `--trust-key`, environment, automatic enrollment, service-private-key
  fallback or other caller-controlled authority source. **That file does
  not currently exist** on `x1-370` and was not created by the patch.
- The owner approval must be purpose-bound to
  `auto-assist:canonical-runtime-grant`, carry fixed schema and `Ed25519`
  algorithm/key ID, match the *complete canonical manifest SHA-256* including
  its runtime, model, paths, resource slots, roster-related manifest content,
  signed `approved_by`/`approval_id`, and exactly match `generation` plus
  `expected_current_generation`.
- Receipt timestamps are milliseconds UTC, with at most a **5-minute grant
  window**, a maximum **15-second future-skew allowance**, and a 32-hex-character
  nonce. Canonical signed bytes are the ASCII prefix
  `auto-assist:canonical-runtime-owner-approval:v1\n` followed by sorted
  compact UTF-8 JSON **without** `signature`. Signature is canonical unpadded
  URL-safe base64 of the owner's Ed25519 64-byte signature. Duplicate JSON
  keys, nonfinite literals, unknown fields, bool-as-integer, noncanonical
  signatures and special-file/symlink receipts are rejected.
- Signature/freshness is rechecked at the beginning of the actual Neo4j
  transaction and again before finalizing the canonical approved state. The
  generation is advanced using `SET s.generation=coalesce(s.generation,0)+1`
  under a Neo4j property write lock *before* changing any runtime approvals;
  a mismatch raises and rolls back the **whole** transaction. A separate
  `MATCH`/`RETURN` then Python compare was vulnerable to a concurrent
  stale-read approval race. The final graph state and approval-evidence JSON
  both record `owner_approval_receipt_sha256` for independent audit.

## Required **external** owner action — not done in this patch

1. An authorized operator and a separately designated security owner must
   review independent runtime provenance, artifact SHA256 and exact process,
   current LAN/fallback access with credentials and identity custody, resource
   ownership/parallelism/queue budget, approved model routing scope, and
   rollback/failover/replay evidence. Synthetic short inferences and this
   private SSH catalog test are not sufficient to claim any Task slot.
2. A designated administrator must provision a **separate** Ed25519 public
   verification key under the above `/etc/assistx/trust/` root-owned path.
   The corresponding *private* signing key must be generated and retained
   under an independent owner-controlled process, never copied to the fleet,
   `.env`, GitHub, API container, or this ChatGPT session. This code **does
   not generate or enroll owner signing keys** and will deny a user-owned
   substitute.
3. After reviewing a **fresh** and complete manifest and independent node
   coverage roster, the authorized external owner may issue a short-lived
   detached receipt in this exact structure:

```json
{
  "schema_version": "assistx.canonical-runtime-owner-approval.v1",
  "scope": "auto-assist:canonical-runtime-grant",
  "signature_algorithm": "Ed25519",
  "signature_key_id": "assistx-runtime-approval-owner-v1",
  "approved_by": "<independently-verified-owner-identity>",
  "approval_id": "<new-authorized-change-id>",
  "manifest_checksum": "<64 lowercase hex SHA-256 of complete validated manifest>",
  "required_roster_sha256": "<64 lowercase hex SHA-256 of canonical required-node roster>",
  "generation": 897,
  "expected_current_generation": 896,
  "issued_at_ms": 0,
  "expires_at_ms": 0,
  "nonce": "<32 lowercase hex from secure randomness>",
  "signature": "<external Ed25519 signature of canonical domain-separated payload>"
}
```

The zero timestamps and placeholder strings above are **schema examples,
not usable approvals**. Actual current generation must be independently
rechecked at issue time; **897 is only the next expected value if canonical
remains 896**. Do **not** reuse old `chat-directive-20261002...` IDs or blindly
extend lease expiry. No retrospective self-signing, guessed identities or
synthetic approval are permitted.

4. Only then, if all other gates remain valid, the reviewed publisher can be
   invoked **once** with both independent roster and detached receipt:

```bash
python3 scripts/approve-runtime-projection.py /secure/reviewed/current-manifest.yaml \
  --apply \
  --required-nodes-file /secure/reviewed/required-node-roster.json \
  --owner-approval-receipt /secure/reviewed/owner-receipt.json
```

This code path requires explicit Neo4j configuration already handled by the
protected approval process. Do **not** put credentials or private signing keys
in command lines or PRs. Read-only validation without `--apply` is the safe
preliminary check; it cannot sign or authorize. The current required-node
roster gate expects multiple independently listed nodes (2–64). Do not
quietly relax that existing scope constraint to grant a convenient one-node
Task without separately reviewed policy changes.

## Validation and release status

Synthetic negative/positive tests use **ephemeral in-memory** Ed25519 keys,
not actual owner identities or signed production lease material. Tests cover
manifest and roster binding, signature tampering, wrong signer, expired/future receipts,
invalid nonce/duplicate JSON fields, root-ownership safeguards, invalid file sources,
unsigned CLI denial *before Neo4j connection*, successful fake write
provenance, a competing generation and an expiring in-flight receipt.
A minimal-permission GitHub workflow runs synthetic tests and source compile
only: **no `--apply`, graph credentials, trust-path provisioning or model
request is ever executed in CI**.

**Production HOLD** remains correct: signed canonical generation **896** was
expired at physical observation; WorkSupply was fresh but zero slots and
`allow_background=false`; machine Task endpoints returned 403. The original
legacy projection auto-renewal unit is disabled by an explicit security
containment override and must stay disabled. Its executable also has two
**unconditional top-level HOLD exits** before any canary or approval I/O.
The `APPROVE_SCRIPT_SHA256` pin was updated solely to match the newly
hardened reviewed publisher (its existing exact-code identity test requires
this); that is **not** an authorization or reactivation. The legacy invocation
still has no signed owner receipt and cannot grant anything. The dedicated
CI tests include the old keepalive source/pin and denial controls. This source change is neither
production deployment nor an authorized grant; see issues #284, #501, #569.
