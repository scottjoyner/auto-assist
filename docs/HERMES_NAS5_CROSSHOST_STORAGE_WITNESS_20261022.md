# Hermes / NAS5 read-only cross-host storage witness — October 22, 2026

**Decision: HOLD.** Draft auto-assist PR #425 and P0 #414; no production cutover is authorized.

## Problem this closes

A Docker bind in production Hermes points at `/media/scott/NAS5` on x1-370. Prior read-only host checks established this is **not a mounted NAS filesystem on x1-370**: `findmnt -T` resolves to the local root ext4 filesystem. The directory's name is not evidence that data is reachable on Beelink. Before approving any storage migration, we must distinguish host-local fallback directory paths from real NAS volume mountpoints.

## Observed cross-host storage topology

Fleet Commander used x1-370's existing `beelink` SSH alias with `BatchMode=yes`, `StrictHostKeyChecking=yes`, `ConnectTimeout=4` and a bounded **read-only** `findmnt` command. This only reads filesystem mount metadata and preserves no block-device identifiers or credentials in the resulting report. It does **not** transfer or enumerate any stored files.

| Node | Host path | Observed filesystem | Exact mountpoint |
| --- | --- | --- | --- |
| x1-370 | `/media/scott/NAS5` | local ext4 root `/` | **No** |
| Beelink | `/nas` | Btrfs | **Yes** |
| Beelink | `/mnt/nas5-output-primary` | exFAT | **Yes** |
| Beelink | `/mnt/nas5-output-direct` | exFAT | **Yes** |
| Beelink | `/media/scott/NAS5` | local ext4 root `/` | **No** |

This is mount-identity evidence only. It does **not** establish that Beelink's three filesystems are healthy at the file level, authoritative custody for the complete corpus, independently administered, accessible from the actual Hermes container, or suitable to be mounted writable.

## Reproducible controls

Source: `tests/hermes_nas5_crosshost_preflight.py`. The CLI is inert by default:

```bash
python3 tests/hermes_nas5_crosshost_preflight.py --json
```

The physical **read-only** opt-in is restricted to x1-370 as UID 1000:

```bash
python3 tests/hermes_nas5_crosshost_preflight.py --physical-readonly-crosshost --json
PYTHONPATH=src python3 -m pytest -q tests/test_hermes_nas5_crosshost_preflight.py
```

The contract fixes the SSH alias and path allowlist, verifies the trusted host key without automatically accepting a new one, bounds output/time, rejects incomplete/duplicate or malformed metadata and never stores reported device sources, IPs, secrets or file contents. All negative cases are offline fixtures. The report is permanently marked `HOLD` regardless of whether the three expected Beelink mounts are present.

## Release consequences

1. Do not mount or remount `/media/scott/NAS5` on either host without separately reviewing possible fallback-data custody. Mounting over an existing directory could obscure local files.
2. Never attach Beelink's `/nas` or exFAT root as writable Hermes storage merely because its filesystem is mounted. Independently approve the exact read-only subtree and separately controlled scratch/output.
3. Keep production Hermes unchanged: the two protected SSD knowledge aliases and the broad NAS-labelled Docker bind remain unresolved.
4. The Beelink SSH probe is controlled by the same fleet operator, **not** an independently signed attestation or rollback-resistant witness.
5. Enforce explicit owner grants, independent revocation/fencing, signed source/image provenance, and reviewed rollback before any deployment.

P0 issue #414 remains open, PR #425 remains draft, and production remains on **HOLD**.
