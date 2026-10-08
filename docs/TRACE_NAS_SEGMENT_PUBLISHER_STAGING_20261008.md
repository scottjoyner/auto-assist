# Trace segment NAS publisher — isolated acceptance handoff

**Decision date:** 2026-10-08 (America/New_York)
**Purpose:** move beyond the 8 MiB monolithic trace backup limit without any
truncation, NAS5 recovery interference, uncontrolled agents or production
worker promotion.
**Authority:** bounded real-execution work approved by the operator, but
this document does not approve unverified NAS writes or general shell execution.

## Release boundaries

`src/assistx/trace_segment_nas.py` is a separately callable publisher for
**already encrypted, locally verified** bundles staged by
`trace_segment_bundle.py`. It is not wired to a timer, worker, dispatcher,
AssistX issuer or existing four-generation NAS backup campaign.

Required caller inputs are a private local source bundle, separate private
local witness directory and pre-existing destination directory on a CIFS
mount, with its **exact expected source and target supplied explicitly**.
For the current read-only host observation, the mapped source is
`//192.168.1.202/fileserver` mounted at `/nas`, including an autofs
wrapper. This CIFS mapping **must not** be assumed to be the authoritative
NAS5 recovery disk /dev/sdd2 or any other recovery volume.

The publisher validates source encryption and HMAC/segment links before
any destination creation. Mount identity is checked at admission and again
during each object publication. Files are placed in
`<destination>/<node_id>/<journal_sha256>/` with no-overwrite
`os.link` after fsync of staged ciphertext; `index.json` lands LAST.
A full remote decrypt/reassembly must equal the original source bytes.
Only after that, a **separate local** HMAC chain is appended/fsynced to
`published.jsonl`. A standalone `verify_published_bundle()` independently
reads the local signed witness and decrypts/restores all remote segments.
Missing witness, different mount identity, path substitution, tampered
ciphertext/index, insecure ownership and suspicious existing archives deny
success. Re-entry with the same content re-verifies and reuses files,
without destructive replacements; witness entries do not duplicate.

## Predict / observe / interpret

**Prediction (for the next *actual CIFS write* acceptance):** Exact mount
identity and private-dir mode must hold. A >8 MiB trace journal should
publish and restore byte-for-byte, and a later fresh process should verify
its locally signed custody index. Concurrent callers should result in only
one signed entry. Missing mount, substituted source, partial upload,
incorrect permissions or damaged ciphertext must not report a valid
witness. A deliberate interruption *after* a complete remote index
but *before* the local witness is committed must fail closed rather
than silently reset witness history.

**Observation (simulated mount tests, not real NAS write):** Real AES-256 GPG
encryption and restoration ran against temporary owner-private directories
with a fixture mount-identity function. The publisher test suite checks
idempotence, interruption/resumption, copy-time mount disappearance, bad
existing permissions, tampering, missing/torn witness, wrong node and
concurrent invocation; independent restore reads the witness and the
ciphertext in a separate verification call. The actual host CIFS mount
was inspected **read-only**: `findmnt -rn -T` returns both
`autofs systemd-1 /nas` and
`cifs //192.168.1.202/fileserver /nas`. The strict mount validator
was corrected to accept this exact pair, not autofs alone or a mismatched
CIFS source. No production NAS archive object was written this pass.

**Interpretation:** This establishes a promising local functional staging
gate. It does not establish a complete production offload, because
`os.link` and directory fsync compatibility/permissions on the actual
CIFS filesystem have not been validated by a bounded real write, and a
local HMAC witness is **not** an independent remote immutable/WORM record.

## Explicit unresolved gates

1. Authorize and perform a bounded real-CIFS staging write to a dedicated
   empty namespace only after source/mount-owner confirmation, with
   independent restore from fresh credentials and no touching legacy
   archived generations or NAS5 recovery data.
2. Provision an independent durable signing/witness destination outside
   both the mounted NAS and the controller's writable local filesystem.
   Protect against deletion/replacement of *both* ciphertext and ledger.
3. Design two-phase intent/commit and recovery for power loss after the
   final index lands but before witness commit. Current publisher
   deliberately blocks that ambiguous state; it does not auto-repair.
4. Add bounded incremental source reads, resumption metrics, backlog
   thresholds, mount-churn alerts, and key escrow/rotation. Current
   implementation verifies full locally staged source in memory.
5. Only after proven long-duration offloading should the worker's 7 MiB
   journal admission stop be reconsidered. Never trim local trace history.

The protected production AssistX issuer still lacks real deployment and
physical committed-claim acceptance. Continue from [AssistX PR #119](https://github.com/scottjoyner/auto-assist/pull/119),
[production issue #120](https://github.com/scottjoyner/auto-assist/issues/120),
and [NAS issue #127](https://github.com/scottjoyner/auto-assist/issues/127).
