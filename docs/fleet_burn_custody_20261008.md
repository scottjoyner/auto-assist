# Fleet OpenCode custody before cross-node burn aggregation

**Purpose:** audit expected OpenCode archives on all known producer nodes without counting copies as new token consumption. **Status:** observational read-only, no provider quota verification or live model admission.

## Predicted outcome before validation
Per-host `latest.txt` and manifest should provide source-claimed host, archive filename, size, timestamp and SQLite quick-check. Verifying the manifest alone will not establish archive SHA integrity, a unique session/part identity or tokens billed by upstream. Any missing, mismatched, stale or cloned archive must be visible and **excluded** from the token aggregation gate.

## Observational method and input trust
- Inventory six explicitly listed known producer hosts under `/nas/opencode-fleet/<hostname>`. The configured NAS custody route is CIFS `//192.168.1.202/fileserver`. This path is **not** the authoritative NAS5 exFAT owner mount.
- The publisher fails closed when the host filesystem type or mount source differs from the checked identity. No writes, deletes, archival pruning or snapshots on NAS.
- Validate host/filename agreement, strictly bounded `latest.txt`/manifest sizes, strict UTC timestamp, claimed SHA256 digest structure, exact archive-byte size and reported quick-check. Quarantine invalid or absent inputs, never infer zero sessions or usage.
- Large archive data SHA256 remains an explicit deferred check. Optional `--verify-max-mib` hashes only small archives from the read-only source; no automatic full-harvest checksum on the 15-minute burn loop.
- Duplicate archive digests across hosts are surfaced as potential replicated custody; session counts from manifests must not be summed.
- No raw prompts, user data, session IDs, paths to user credentials, tooling outputs or API keys are emitted to the dashboard.

## Reporting and audit
- `scripts/fleet_burn_custody.py` generates `fleet-burn-custody/v1` inventory; `render_fleet_burn_custody.py` creates an HTML-escaped static fragment for existing authenticated AssistX trace page.
- `fleet_custody_chain.py` appends SHA256-linked custody observation receipts to a separate private JSONL ledger. Existing entries are checked prior to append, and corruption fails closed. SHA chains are tamper-*evident* against accidental modification, not cryptographically authenticated against a privileged adversary.
- Unknown official quotas, ambiguous alias groups, unverified archive bytes and cross-node sessions **never** qualify for admission, exhaustion forecasts or additive token totals.
- No existing fleet trace history is trimmed or deleted.

## Runtime acceptance gate
Run synthetic adversarial tests first, shadow publisher against NAS manifests, then install pinned private copies and a separate hourly read-only systemd timer. Verify the static Jinja include, authenticated HTTP 200 and unauthorized 401, service/timer success, receipt continuity, and rollback backup. Only the dashboard template can be changed live; no API router or executor restart.

**Still required for true fleet-wide burn accounting:** independently verify archive contents, export event-time per-part token records with source manifest hash and immutable producer identity, reconcile identical session/part IDs across mirrors, quarantine conflicting records, test replay idempotency, and prove independent quotas/reset times from official provider account sources. Until then `actual_fleet_token_count=null`.
