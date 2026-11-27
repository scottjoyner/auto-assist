# Outbox notes under resource pressure (R10 session 4)

This note is documentation only. It records **read-only** observation commands and the
meaning of the existing Round-4 marker. It proposes **no drain, requeue, claim, delete,
schema change or reconciliation** — those remain an owner decision, unchanged from
Rounds 4/5/6/8/9. No counts are asserted in this note; run the commands and read the
current output instead.

## 1. Read-only audit tool

The standalone, stdlib-only audit tool is `tools/audit_outbox_ro.py`. It was added on
`agent/outbox-audit-tool-r8-20261126` (commit `e56c7df`), so it is **not present in this
base** (`b0b4e448`, the parent of that commit). Run it from its own worktree; do not copy
or edit it here:

```sh
cd /home/scott/git/wt-assistx-outbox-audit-r8-20261126
python3 -m py_compile tools/audit_outbox_ro.py
PYTHONDONTWRITEBYTECODE=1 python3 -B tools/audit_outbox_ro.py /home/scott/.assistx_outbox.db
```

Signature: `python3 tools/audit_outbox_ro.py DB_PATH [--table TABLE]`, with `--table`
defaulting to `outbox`. Exit status: 0 = audit completed (this is **not** a health
verdict), 1 = database unreadable or expected table absent, 2 = usage error. Its output
reports the open mode, file mtime/size, detected columns, row/status/attempt/owner
distributions, quarantine-marker total and timestamp ranges.

Safety properties (from the tool itself): the DB is opened as `file:<path>?mode=ro`
with `PRAGMA query_only=ON`; no INSERT/UPDATE/DELETE/DDL is issued and no rows are
claimed, deleted or marked. Only whitelisted columns are selected; payload bodies are
never read or printed. `claim_owner` is opaque — recognised `quarantine:` markers are
rendered, every other non-null owner (a claim token, possibly credential-looking) is
printed as `<redacted>`.

Under pressure keep it this way: no temp copy of the DB, no rebuild, no backup/restore,
no `VACUUM`, no WAL checkpoint, no service restart. `PYTHONDONTWRITEBYTECODE=1 python3 -B`
avoids writing `.pyc` files.

## 2. Round-4 quarantine marker

The local worker outbox `/home/scott/.assistx_outbox.db` carries the reversible label

```
quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126
```

in `claim_owner`. It was applied by an owner action in Round 4 (one explicit
`BEGIN IMMEDIATE` transaction, parameterized `UPDATE outbox SET claim_owner = ... WHERE
status = 'pending' AND attempt_count = 9 AND claim_owner IS NULL`; see
`/home/scott/opencode-reviews/round4-session1-outbox-20261126.md`, §4). The token encodes
the reason (stale attempt 9 beyond `max_attempts=5`) and the round/date suffix
`r4s1-20261126`. Status, attempt counts, timestamps, `event_id` and payloads were not
changed by that action, and nothing was deleted. The audit tool reports these markers
under "Claim-owner counts" and "Quarantine markers total"; it does not alter them.

## 3. Router-side outbox is a different store

The router's auto-assist dispatcher reports its own outbox/pending counts in its health
endpoint. Read it without credentials or writes:

```sh
curl -fsS http://127.0.0.1:8088/health
```

Those numbers come from a **different store** from `/home/scott/.assistx_outbox.db`.
They are counts only: do not reconcile the two, do not infer that rows moved between
them, and do not treat any difference as drift requiring a drain. Each source should be
described with its own path/endpoint and observation time.

## 4. Owner decision required

No drain, reconciliation, claim release, requeue or marker removal is proposed or
performed here. Any such action needs an explicit owner decision, a verified backup and
a rollback plan, as in Round 4. This work only documents how to look, read-only, without
inventing counts or touching live state.
