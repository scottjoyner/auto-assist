# Issue #118 — Independent legacy ACK custody census
Date: October 9, 2026, x1-370 to Beelink. Research only; live remediation HOLD.

## Verified source and scope
- Deployed safe trace drainer source remains SHA256 c2759e93f8d4e201153e92c04e93933b621ed41d153c128f79472c7cc63f726b.
- Live collector source remains SHA256 fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe.
- Local ACK census scanned all 173 regular legacy ACK JSON files, the corresponding 173 local source file sizes, 173 local manifests and 173 local ready files. No archived source payload was read during the metadata census.
- Beelink read-only audit used SSH, verified /nas/fileserver/... lies on the expected /dev/sdd2 Btrfs mount UUID 0880694b-51c8-42be-b8d2-c8ac119f2b58, and examined remote archive file metadata and bounded manifest/ready sidecars. No remote archive payload was read during the full census.

## Full 173-ACK metadata census
| Check | Observed |
|---|---:|
| Structurally valid local ACK + archived file stat + ready + manifest | 173 / 173 |
| Legacy ACK without ready_sha256 | 173 / 173 |
| ACK source_release BLOCKED | 173 / 173 |
| Expected NAS UUID and exact destination path in ACK | 173 / 173 |
| Corresponding NAS archive present | 173 / 173 |
| NAS archive stat size equals local ACK | 173 / 173 |
| Remote NAS manifest SHA256 equals retained local manifest SHA256 | 173 / 173 |
| NAS ready marker present | 0 / 173 |
| NAS ready marker absent | 173 / 173 |

The retained local archive payloads aggregate 501,320,205 bytes by stat only. An archive of matching size is NOT a full content-integrity proof. The remote manifest comparison IS a SHA256 comparison of all 173 bounded manifest sidecars.

## Bounded three-archive payload witness
Read-only stratified probe chose the smallest, median and largest of the 173 archived file sizes:
- 3/3 retained local compressed archive payloads matched their recorded SHA256.
- 3/3 corresponding Beelink archive payloads independently matched those recorded SHA256 values.
- Total compressed bytes read from each side: 8,781,977 (~8.78 MB).
- Results recorded as aggregate counters, without source paths, payload contents, or archive names.
- Remaining 170 NAS archive payload contents have NOT been independently SHA256 verified in this census.

## Engineering acceptance
- audit_legacy_ack_remote.py: bounded metadata auditor. No payload copying, writes, ACK edits, ready backfill or source release. It rejects unsafe local names and symlink/oversized local sidecars and verifies Beelink filesystem UUID before remote metadata inspection.
- test_audit_legacy_ack_remote.py: 8/8 synthetic local/remote-contract tests passed.
- The separately isolated drainer three-part ready receipt patch remains 11/11 green in a fresh patch replay against its frozen original, but deployment is intentionally held because all 173 legacy ACKs lack new ready proof.

## Release and NAS custody decisions
- DO NOT infer that 173 NAS payloads are fully verified from their sizes and manifest hashes; 170 remain unverified at the payload-byte level here.
- DO NOT install ready files or modify 173 ACKs automatically. Even if archive+manifest SHA prove custody, sidecar repair needs source-owner approval and create-only/no-clobber negative collision tests.
- DO NOT deploy the strict drainer while its admission path would reject all 173 existing legacy ACKs. Keep the originals and all existing ACKs unchanged; stage migration separately with per-item owner decisions.
- The safe-drain status at 2026-10-09T20:05:01Z was held_admission with 281 pending; watchdog at 20:07:01Z counted 281 unACKed and 173 ACKed but retained. The local SSD was 96% used with ~144 GB free.
- NAS5 Beelink evacuation remains active and must retain priority. No general NAS content digest sweep should start until storage admission permits additional bounded read load.
- Issue #118 historical 152-cursor adoption and issue #117 full retention remain distinct and OPEN. PR #208 stays DRAFT. CI and target-ext4 crash/restore acceptance remain open.

## Smallest owner-approved next gate
When Beelink recovery has bandwidth headroom, verify the remaining 170 remote archive SHA256 values in **bounded read-only batches**, tracking per-file hashes and immutable receipts privately. Reconcile mismatches/conflicts before any repair. Separately approve create-only NAS ready-marker installation and old ACK ready_sha256 attestation, with full rollback and no source deletion. Only after independent archive custody evidence and exact GO admission may the reviewed drainer be considered for staged deployment.

**No production archive, NAS object, local ACK, source, cursor, collector, service or configuration was mutated during this census.**

