# Hermes storage topology: independent mount and scratch provenance preflight

Date: October 22, 2026. Fleet Commander on x1-370. Related: draft auto-assist PR #425; open security issue #414. Decision: **HOLD**. This document is not a production cutover plan or approval.

## New host discovery

Read-only verification inspected directory metadata and the nearest mounted filesystem via findmnt, without reading any protected corpus file, copying credentials, allocating space, mounting/unmounting disks, or changing Docker.

| Host path | Observed filesystem | Important finding |
| --- | --- | --- |
| /media/scott/SSD_4TB | Separate ext4 mount | Protected research tree lives on this separate device; about 597.8 GiB currently free |
| /home/scott/knowledge | Filesystem symlink into /media/scott/SSD_4TB/knowledge | This is a protected-data alias and cannot be used for production Hermes RW mounts |
| /media/scott/NAS5 | **Root** filesystem (/), not a mountpoint | Name is not proof of an attached NAS; read-only findmnt and mountpoint checks agree |
| /home/scott/.cache | Root filesystem (/), private 0700 directory | About 220.7 GiB currently free; research fixture storage only; no capacity reserved |
| /home/scott/git | Root filesystem (/) | Shares the device with the research scratch; not independently administered custody |

The production Hermes container still binds **16 mounts, nine RW**, including both a writable SSD ancestor and a writable alias to the protected corpus. Its bind to /media/scott/NAS5 therefore currently maps a host directory on the local root filesystem, not an independently verified NAS mount. This does not prove what applications have written there; no directory contents were examined.

**Do not automatically mount NAS5, rsync or migrate any data, reclaim storage, remove the container, or edit these production binds.** A storage owner must independently confirm whether the labelled path should be an active mount and whether any local fallback data require custody review before remounting over it.

## New testable contract

The read-only verifier lives at tests/hermes_storage_topology_preflight.py. It only collects approved host-path metadata, canonical paths, symlink identity, owner/mode, free-block estimates and findmnt target/source/type. It never inspects Docker environment values, secrets or file contents. Default invocation produces a no-probe HOLD report; only the explicit flag performs physical read-only checks.

Commands:

    python3 tests/hermes_storage_topology_preflight.py --json
    python3 tests/hermes_storage_topology_preflight.py --physical-readonly --json
    PYTHONPATH=src python3 -m pytest -q tests/test_hermes_storage_topology_preflight.py

The verifier rejects unexpected source paths or malformed topology records and flags: missing SSD mount, changed corpus symlink, scratch on the protected SSD, insecure scratch ownership/mode, less than 20 GiB current research headroom, NAS5 not actually mounted, and research scratch sharing the root device with mutable repositories. Available bytes are a **snapshot**, not a reservation or a quota. Even if all local topology checks pass, the resulting release decision is HOLD.

## Smallest safe next release-validation steps

1. **Storage custody:** establish the intended true NAS5 host mount state and whether /media/scott/NAS5 contains local fallback data before any mount action. Do not infer healthy NAS merely from directory existence.
2. **Corpus custody:** nominate an exact, narrow read-only knowledge source; independently authorize and verify its contents, symlinks and filesystem ancestry without exposing the protected SSD root.
3. **Write custody:** nominate a new independently reviewed dedicated Hermes scratch/output area with quotas, backup/restart semantics and no writable route back into protected evidence or mutable source repositories. The private /home/scott/.cache test directory is not automatically a production choice.
4. **Candidate image:** sign and review exact source/image provenance and a restricted Compose manifest. No private SSH bind or inherited production credential environment.
5. **Owner and availability:** run real task ingestion only with owner-bound receipts, independent revocation/fencing and fail-closed offline behavior. Prove tested rollback and receive independent signoff before any production cutover.

The source PR and its existing synthetic normal-entrypoint checks do not qualify full real-data ingestion or release safety. Keep issue #414 OPEN, PR #425 draft, and production Hermes unchanged.

## October 24 update: verified `/nas` is a separate network mount

The original warning about `/media/scott/NAS5` **remains correct**: that name resolves to the local root ext4 filesystem, not NAS storage. A broader read-only `findmnt` inventory found that x1-370 separately exposes a **real network share at `/nas`**, layered under a generated systemd automount. At observation time `nas.automount` and `nas.mount` were active; `findmnt --target /nas` returned `autofs` (`systemd-1`) and a CIFS mount sourced from `//192.168.1.202/fileserver`. `df` showed approximately **37 TB available** on that CIFS share. `/tank/recovery` was independently observed as NFSv4 from `192.168.1.79:/tank/recovery` with about **1.1 TB available**. The protected SSD had about **549 GB available**; the local root about **147–148 GB**, depending on observation time.

The preflight now includes `qualify_network_mount_rows()` and `analyze_network_nas()`, which **do not enumerate file contents, read secrets, create files, mount shares, or reserve capacity**. The witness recognizes one exact CIFS entry at `/nas` with optional `systemd-1` `autofs` layering, rejects the root-filesystem fallback, missing CIFS layer, changed network source, unexpected layers, duplicate records and overlong output, and reports `remote_mount_observed` independently of `hermes_output_approved=false` and `independent_evidence_custody_verified=false`. If `/nas` disappears or drifts, the physical preflight records an explicit `actual_network_nas_unverified` blocker; even when observed correctly, it retains `network_nas_write_and_custody_not_approved` and the original local NAS5 mislabel blockers. The new negative/positive tests use synthetic findmnt output; they do not access production data.

A verified network mount is only a **candidate location** for a *separately scoped* Hermes output workspace. Before any future Docker/Compose bind, operators must independently approve the exact subdirectory, ACL/UID permissions, quotas, credentials, identity and retention, ensure the network share is really mounted at bind creation and cannot silently fall back to local root (including after network loss), qualify disconnected/restart behavior and external custody, and ensure no write alias into the protected signed input tree. Never bind the entire `/nas` root or the misleading `/media/scott/NAS5` directory. Production Hermes remains at 16 mounts/9 RW, and production deployment remains **HOLD**.
