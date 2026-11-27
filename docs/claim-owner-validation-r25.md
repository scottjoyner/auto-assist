# Claim-owner validator hardening — accepted/rejected shapes (r25)

**Status: TEST + DOC ONLY. NO PRODUCTION CHANGE, NO DEPLOY, NO LIVE DB WRITE,
NO MCP REGISTRATION.**

This slice hardens the contract around the R23 optional `claim_owner` parameter
(`src/assistx/outbox_client.py::_validate_claim_owner`). R23 already added the
validator and its core tests; this document records the exact accepted/rejected
shape boundary and the tests that pin it. No bypass was found, so the production
validator was left unchanged.

## Contract at a glance

The optional keyword-only `owner` of `OutboxClient._claim_next()` is validated
**before any SQL write**. A rejected value raises `ValueError` and the row stays
`pending` with `claim_owner IS NULL`, so an ordinary default claim can still take
it. When omitted, the historical `uuid4().hex` token is still minted.

| Shape | Example | Result |
|---|---|---|
| Valid session-shaped id | `sess-` + 32 lowercase hex chars | **Accepted**, stored verbatim, returned on the entry |
| Any printable, non-blank label | `worker-x1-370-01` | **Accepted** |
| Length exactly 128 | `"s" * 128` | **Accepted** (inclusive upper bound) |
| Length 129 or more | `"s" * 129`, `"s" * 256` | Rejected — `1..128 characters` |
| Empty string / non-string | `""`, `123` | Rejected — non-string/blank guard |
| Whitespace-only | `"   "`, `" \t "` | Rejected — `owner != owner.strip()` |
| Leading/trailing whitespace | `" sess-x"`, `"sess-x "` | Rejected — same strip check |
| Leading/trailing Unicode space | `\u00a0` (NBSP) around a label | Rejected — NBSP is non-printable in Python |
| NUL / control escapes | `\x00`, `\x1b`, `\x7f`, `\r`, `\n`, `\t` | Rejected — `not owner.isprintable()` |
| Unicode line separators | U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR | Rejected — Unicode category Zl/Zp is non-printable |
| Zero-width space | U+200B | Rejected — Unicode category Cf is non-printable |
| `quarantine:` prefix, any ASCII case | `quarantine:x`, `QUARANTINE:x`, `QuArAnTiNe:x` | Rejected — case-insensitive prefix check |
| Unicode quarantine lookalike | `QUARANTİNE:x` (Turkish dotted I) | Accepted, and **safe**: SQLite `LIKE` folds ASCII case only, so this is not an operator hold |

## Why no production change was needed

Two apparent edge cases were checked against real behavior and found sound:

1. **Whitespace-only and Unicode separators.** Python's `str.isprintable()` returns
   `False` for Unicode categories `Zl`/`Zp`/`Zs` (except ASCII space) and `Cf`, so
   U+2028/U+2029, NBSP and zero-width space are all rejected; ASCII-space-only
   strings are caught by the `owner != owner.strip()` check. No bypass.
2. **Unicode quarantine lookalikes.** The prefix guard uses
   `owner.lower().startswith("quarantine:")` while the SQL guard uses
   `claim_owner NOT LIKE 'quarantine:%'`. SQLite's `LIKE` folds only ASCII A–Z,
   so a Unicode lookalike that survives the validator also fails to match the SQL
   hold guard — it cannot impersonate an operator quarantine. Accepting it is
   correct; widening the validator would reject legitimate labels without closing
   any gap.

Lease recovery is also pinned: a valid session-shaped owner is stored verbatim, a
different valid owner recovers the row exactly once after lease expiry, the stale
owner's release is fenced out, and the same owner may reclaim its own expired
lease.

## Live MCP integration is still owner-gated

This remains source-only hardening, not a deployed integration. Live use still
requires owner decisions and work outside this slice:

1. **Coordination DB** — the live read-only `sensorgraph-coord` surface reports
   `available=false / coordination_db_absent_or_unconfigured` unless `SG_COORD_DB`
   is provisioned. A durable DB, schema and live-DB policy decision are required
   before any MCP session can pass its id to this path.
2. **Session identity policy** — who may set `owner`, which string is authoritative
   (principal binding, token lifecycle, disclosure rules) and how an MCP session id
   maps to a claim owner. `_validate_claim_owner()` checks shape only; it does
   **not** authenticate the caller or bind the label to a principal.
3. **Exposure/quota decision** — review, register and enable any MCP surface, and
   set measured quotas/fencing across hosts. No capacity claim or SLO is made here.

The outbox and any coordination store stay **separate SQLite files with separate
envelopes** (no shared transaction, lock or token). MCP's short in-memory heartbeat
does not extend the outbox's passive claim lease.

## Tests

See `tests/test_claim_owner_validation_r25.py` (all fixtures are temp SQLite files
under SSD-contained pytest temp space, or in-memory SQLite). Exact commands and
results are in `/home/scott/opencode-reviews/round25-session4-assistx-20261126.md`.
