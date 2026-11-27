# Optional caller-supplied claim owner (r23) — source-only

**Status: SOURCE-ONLY. NO DEPLOY, NO LIVE DB WRITE, NO MCP REGISTRATION.**

This closes the gap named in `docs/mcp-outbox-bridge-r22.md`: `_claim_next()` had no
caller-supplied owner parameter and always minted a fresh `uuid4().hex` into
`claim_owner`, so an MCP session id could not be recorded on a claim.

## What changed

`OutboxClient._claim_next()` gained one optional keyword-only argument:

```python
owner: Optional[str] = None
```

- **Default (`owner=None`)** — unchanged. A fresh `uuid4().hex` token is minted and
  written to `claim_owner`, exactly as in R11–R22. Every existing public caller
  (`flush()` → `self._claim_next(exclude=seen)`) is untouched.
- **Supplied** — the validated label is written to `claim_owner` and returned on the
  `OutboxEntry`. It remains the *release fence*: `_release()` still requires the exact
  `claim_owner` value, so presenting any other owner (including another session's id)
  is rejected. Lease recovery, the `BEGIN IMMEDIATE` transaction, the `exclude` clause
  and the R11/R12/R13 quarantine guard in both the SELECT and the guarded UPDATE are
  unchanged.

A safe seam existed, so a small production change was made; no "no safe seam" fallback
was needed.

## Validation (conservative, fail-closed)

`_validate_claim_owner()` rejects anything that is not a short, unambiguous label,
*before* any SQL write:

- non-string or empty owner;
- length above `MAX_CLAIM_OWNER_LEN` (128 characters);
- non-printable characters or control characters (including `\n`, `\t`, `\x00`);
- surrounding whitespace;
- a `quarantine:` prefix, compared case-insensitively because SQLite `LIKE` is
  ASCII-case-insensitive. A caller therefore cannot write a value that
  `_QUARANTINE_CLAIM_GUARD` would treat as an operator hold.

A rejected owner raises `ValueError` and leaves the row untouched (still `pending`,
`claim_owner IS NULL`), so the row remains claimable by an ordinary default claim.

## Boundaries / owner-gated work still required

This is **not** a deployed MCP integration. Live use remains owner-gated and needs:

1. **Coordination DB** — `SG_COORD_DB` is unconfigured in the reviewed MCP server;
   its coordination surface defaults off and exposes no write tool. A durable DB,
   schema migration and live-DB policy decision are required before any session can
   call this path from MCP.
2. **Session identity policy** — who may set `owner`, what string is authoritative
   (principal binding, token lifecycle, disclosure rules), and how a session id maps
   to a claim owner. The code records a caller-supplied label; it does **not**
   authenticate that caller or bind the label to a principal.
3. **Exposure decision** — review/register/enable of the MCP surface and measured
   quotas/fencing across hosts. No SLO or capacity claim is published.

The outbox and any coordination store remain **separate SQLite files with separate
envelopes**; there is no shared transaction, lock or token between them. MCP's 1 s
in-memory heartbeat does not extend the outbox's 90 s passive claim lease.

## Validation performed

See `/home/scott/opencode-reviews/round23-session4-assistx-20261126.md` for exact
commands, fixture shapes and results. Tests use only in-memory SQLite and a temp
SQLite file under SSD-contained pytest temp space; no live DB was opened for write.
