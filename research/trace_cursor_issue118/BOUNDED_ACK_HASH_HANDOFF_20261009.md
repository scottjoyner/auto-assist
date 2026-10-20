# Issue #118 — resumable NAS archive hash witness, October 9, 2026

**Disposition: research-only, production HOLD.** This slice builds on the complete 189/189 legacy NAS archive+manifest metadata census, verified Beelink backing UUID and the missing ready-marker defect. It is **not** a remote receipt repair or collector migration.

## Current operational observations
- Date/time of observed host state: 2026-10-09 ~17:55–18:01 EDT on x1-370.
- Safe-drain held_admission at 2026-10-09T21:35:01Z: 289 pending. Watchdog 2026-10-09T21:47:03Z: 292 pending ACK, 189 ACKed but locally retained, 481 raw backlog. Different timestamps/semantics; do not add unlike counters.
- Local SSD_4TB ext4 remains approximately 96% utilized with ~142 GiB available.
- Current deployed drainer SHA256: c2759e93f8d4e201153e92c04e93933b621ed41d153c128f79472c7cc63f726b.
- Current deployed collector SHA256: fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe.
- Live NAS admission check at 17:55 returned L1 GO: util 44.5%, iowait 8.91%, Btrfs errors zero. NAS5 Beelink evacuation was actively progressing (3-sec remote rsync aggregate ~76 MB disk writes); do not compete unnecessarily.
- The metadata-only ACK census returned 189 valid local ACK structures, 189 remote payloads present and size-matched, 189 remote manifests SHA256 matching local, **189 NAS .ready absent**, all 189 local legacy ACKs missing ready_sha256. Content hash coverage is a separate requirement.

## Executed bounded content witness
- One bounded unjournaled earlier research check selected eight dispersed ACK archives, 14,984,708 archive bytes per host, and checked 8/8 local SHA256 and 8/8 Beelink SHA256 against their ACK digests. This unjournaled observation is **not** counted as independently tracked per-item progress.
- A second research check repeated a deterministic eight-archive batch, same size; 8/8 local SHA256 and 8/8 Beelink SHA256 matched. The successful batch was committed to a **private** append-only, fsync'd local ledger (permissions 0600) storing hashes of archive names, expected original digests and bytes, but no source names, user archive contents or user logs. **Cumulative uniquely journaled coverage: 8/189.**
- The latter official L1 decision still returned GO with util 15.7% but iowait 26.84%. This is uncomfortably near the official 30% threshold, so further remote hash batches were halted voluntarily to protect NAS5, rather than exploiting GO until HOLD.
- The bounded research-only script now imposes a stricter threshold (util under 70%, iowait under 20%) *in addition to* the official fresh L1 GO. Each invocation validates x1 ext4 source device, Btrfs UUID on Beelink, <=8 archives and <=24 MiB total, low-priority remote reads, source/remote inode+mtime/ctime stability during SHA256, and an exclusive local journal lock.
- Any mismatch, source generation change, stale/contradictory prior journal entry, missing identity or high pressure aborts without recording completion or authorizing repair. It never opens a source JSONL record, creates a NAS object, writes a remote marker, updates a legacy ACK, resets a cursor, deletes a source or enables production migration.
- Synthetic test suite for the journaled batch selector, fail-closed admission and journal integrity: 8/8 green locally. The separate historical legacy ACK metadata audit: 8/8 green; isolated three-part ready/ACK patch fresh replay: 11/11 green.

## Exact operational boundaries
- Only private local research journal and aggregate evidence files were created/updated; live drainer, trace collector, ACKs, NAS archive contents, NAS5 recovery process and user checkpoint state are unchanged.
- The per-file private receipt ledger is *deliberately omitted from GitHub* and must not be copied to public issue comments.
- Despite the partial content-hash success, 181 of 189 current legacy ACK archive payloads remain outside the independently journaled proof. The previous three-archive stratified historical observation is not added to this count without identity-level deduplication.
- Source-owner approval is still required before any create-only remote .ready repair, local ACK attestation rewrite or deployment of the strict drainer. Full historical 152-cursor lineage and NAS5 retention gates remain separate.
- Continue the private journal in additional small batches only when both official L1 GO **and** stricter research headroom are satisfied and the NAS5 evacuation has a safe read budget. Nothing should override a HOLD.

**Review source:** bounded_ack_hash_witness.py and test_bounded_ack_hash_witness.py.

