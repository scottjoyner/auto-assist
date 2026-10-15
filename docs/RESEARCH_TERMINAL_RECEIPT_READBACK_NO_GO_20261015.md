# After uncertain COMMIT: read-only terminal classification, never replay

**October 15, 2026 | x1-370 | Research-only / critical NO-GO**

## Context and intended decision

[Auto-Assist research #389](https://github.com/scottjoyner/auto-assist/pull/389)
uses an actual self-SIGKILL child with two separate disposable SQLite
files. It demonstrates that a lost acknowledgment does not tell the
caller whether the external anchor checkpoint was committed.

This proof adds *read-only* terminal classification after child death.
It does **not** reapply an effect, sign a receipt, consume a challenge,
alter a checkpoint, authorize a retry, or repair diverged systems.
Every result contains `release_authorized=false` and
`retry_authorized=false`, including normal, fully-consumed evidence.

## Mechanism

`scripts/research-terminal-receipt-readback.py` accepts two existing
specifically named disposable SQLite files under the same pytest tmp
directory and a synthetic sentinel. An exact caller-supplied expected
record binds resource ID, term, expected previous and next sequence,
generation, checksum, revision digest, previous/new nonce, challenge and
receipt digest. The expected digest is derived from the original signed
claim provided by *the test*. This is not an independently protected
trust-root or verified signer origin.

It opens source and anchor through fresh `mode=ro` connections, uses
`PRAGMA query_only=ON` and `PRAGMA integrity_check`, and inspects
resource effect and receipt rows, anchor high-water, spent challenges
and consumed receipts. It samples both databases **twice** and denies
when snapshots differ. SQL column names are fixed. It has no CLI
acceptance entrypoint and uses no production service, keys, networking
or privileged fleet mutations.

Each SQLite snapshot is internally consistent; **the two databases do
not share one atomic read transaction**. A DB admin can still fabricate
matching rows or replace/restore files. Repeated matching readback is
evidence about these two disposable copies, not independent authority.

## Actual child death recovery: expected HOLD classifications

| Child termination boundary | Source | Anchor | Reported verdict |
| --- | --- | --- | --- |
| Before source COMMIT | No committed effect | Previous seq | SOURCE_NOT_OBSERVED_HOLD |
| After source COMMIT, before anchor | Exact committed source effect | Previous seq | SOURCE_COMMITTED_ANCHOR_PENDING_HOLD |
| After anchor DML, before anchor COMMIT | Exact committed effect | Previous seq, ledgers rolled back | SOURCE_COMMITTED_ANCHOR_PENDING_HOLD |
| After anchor COMMIT, before ACK | Exact committed effect | Next seq, exact consumed/spent ledgers | ANCHOR_CONSUMED_OBSERVED_HOLD |
| Normal completion | Exact committed effect | Next seq, exact consumed/spent ledgers | ANCHOR_CONSUMED_OBSERVED_HOLD |

These were checked against **real SIGKILL child processes** with fresh
read-only parent connections under both SQLite `DELETE` and `WAL`
journaling. No production process or host is killed.

## Adversarial denial coverage

- Invalid expected receipt/effect contract, missing source/anchor,
  symlink substitution, malformed or inaccessible table schemas,
  and changed evidence across scans are all `NO_GO`.
- Contradictory source receipt rows, source effect changes, missing
  consumed ledger with advanced anchor, receipt digest mismatch,
  challenge mismatch, and anchor advancing beyond the expected
  committed sequence all yield explicit NO-GO.
- **Critical restore counterexample:** restore the original anchor
  database from a pre-consumption snapshot after a completed receipt.
  The classifier reports `SOURCE_COMMITTED_ANCHOR_PENDING_HOLD` —
  indistinguishable from a genuine first-time source-only commit. It
  DOES NOT authorize retry or even conclude the anchor was never
  previously advanced.
- **Critical signer/DBA collusion:** if the same actor can alter the
  *expected signed record*, source rows, anchor ledger, and public-key
  pin, these consistent rows can still be fictional. The classifier
  performs no separate Ed25519 receipt verification; that was exercised
  in earlier research #384/#385, and no independently governed
  receipt signer is available.
- Two stable reads do NOT establish a frozen resource after the final
  read, external monotonicity, active owner-term fencing, power-loss
  endurance, immutable file/host identity or network split-brain denial.

## Reproduce

```sh
PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
    tests/test_research_terminal_receipt_readback.py \
    tests/test_research_receipt_subprocess_crash_matrix.py
```

Only isolated pytest `tmp_path` databases and synthetic signing keys
are used. The classifier returns no operational recommendation beyond
**HOLD pending independently governed terminal evidence**.

## Next independent authority gate

An approved separately administered resource/anchor pair must provide
independently pinned receipt signer identity and COMMIT-effect witness,
hardware- or service-protected monotonic checkpoint across backup and
restore, an authenticated exact-term fence, and readback provenance
trusted against resource DBA / anchor admin / host OS / signer collusion.
Until those custody boundaries exist, observing source-only COMMIT does
**not** authorize replay, and observing complete consumption does **not**
constitute proof of the actual external effect.

Preserve Auto-Assist #156/#299, Context crypto #46, production graph #31,
hosted CI #45, Router owner-bound receipts, and physical iOS release NO-GO.
**No merges, deployments, production graph/credentials, network, failover,
model admission, TPM/keys or Apple/iPhone changes.**
