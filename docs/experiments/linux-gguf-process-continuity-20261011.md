# Linux GGUF process-continuity source — research-only checkpoint, 2026-10-11

**Status: draft physical source adapter and signed-apply contract. No production signing, canonical approval, Neo4j schema mutation, routing, NAS writes or service restarts.**

Stacked after [AssistX PR #307](https://github.com/scottjoyner/auto-assist/pull/307), [PR #305](https://github.com/scottjoyner/auto-assist/pull/305), [PR #303](https://github.com/scottjoyner/auto-assist/pull/303). Tracks [P0 #304](https://github.com/scottjoyner/auto-assist/issues/304) and [Auto-Router P0 #52](https://github.com/scottjoyner/auto-router/issues/52).

## Problem this slice closes

The previous dual-signed approval envelope bound `process_id` to the exact manifest, but a Linux PID can be reused after a process restarts. A source attestation for PID 4783 does not uniquely name a Linux process over time. Reusing the same PID/model name and relying on a 5-minute observation window alone is insufficient continuity evidence.

## New read-only producer

`scripts/collect_linux_process_continuity.py` executes on **the physical Linux inference host**, never the Auto-Router gateway. The operator supplies an exact separately reviewed expected GGUF SHA-256, byte count, and PID. It:

- Requires the process executable basename `llama-server` and exactly one explicit `-m/--model` argument, with an absolute nonsymlink final file.
- Parses Linux `/proc/<pid>/stat` **field 22** (starttime), safely handling spaces and parentheses in the command name; reads Linux `/proc/sys/kernel/random/boot_id` UUID.
- Hashes the on-disk model bytes (bounded ≤2 GiB), verifies exact expected digest/length and the file's inode/device/mtime/size remain stable.
- Rechecks boot UUID, full argv bytes, same model path and process start ticks **after hashing**, refusing obvious restart/model-replacement races.
- Outputs sanitized `linux-gguf-process-continuity-v1` JSON with PID, boot ID, start ticks, exact artifact fingerprint and capture timestamp. Does NOT print the model path or process arguments.
- Always emits `source_kind=unsigned_host_local_proc`, `signature_verified=false`, `model_loaded_attested=false`, `owner_bound=false`, `can_approve=false`, `can_dispatch=false`.

**Important limits:** reading `/proc` plus a hash does NOT prove which model is actually resident in GPU/RAM or that the HTTP listener is owned by this process. The signed physical source service must independently bind actual PID/boot/session and LOADED state, direct authenticated health/completion, model artifact and owner/capacity grants before it may issue a source signature. An operator-running standalone Python script is NOT that trusted signer.

## Signed approval integration

`scripts/runtime_projection_signed_apply_gate.py` now **requires** these independently source-signed fields in each runtime observation:

- `linux_boot_id` — canonical lower-case UUID.
- `process_start_ticks` — positive Linux field-22 start counter; process PID remains separately bound to manifest `process_id`.
- `process_model_sha256` — exact lower-case 64-character GGUF checksum, matching **exactly one** loaded-model `artifact_fingerprint=sha256:<digest>` in the reviewed manifest.

For now the signed contract intentionally **rejects multiple loaded GGUF model instances under one process** until a richer independently observed process-to-model mapping exists. Malformed UUID, zero start ticks, wrong hash and ambiguous model lists all refuse even when test keys correctly sign the altered envelope.

`scripts/approve-runtime-projection.py` now persists `RuntimeInstance.linux_boot_id`, `process_start_ticks` and `process_model_sha256` from the verified signed envelope, inside the canonical transaction. It still refuses missing signer keys or any of **three** required database uniqueness constraints. The transaction also checks whether an existing runtime ID has the same source-signed boot UUID, PID start ticks, PID and GGUF digest. Changes or missing legacy provenance refuse reusing that identity before any graph write. A legitimate restarted process requires a new runtime ID and independently reviewed approval. No operational signer, private key, signature or approval is created.

## Actual Beelink read-only evidence

Fleet Commander used x1-370 and existing authenticated SSH to stream the exact committed Python source to **BEELINK-RYZEN-7-MINI-PC** as `python3 -`, without installing files or starting listeners. It independently verified:

| Check | Observation |
|---|---|
| GGUF exact SHA256 | `c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c` — MATCH |
| GGUF size | 1,246,255,456 bytes — MATCH |
| Process PID | 4783 |
| Linux process start ticks | 2340 |
| Boot UUID | Canonical, hash prefix `3bded808be0d` (raw boot UUID not committed) |
| Identity continuity before/after hashing | PASS |
| Capture source status | **UNSIGNED, NON-AUTHORIZING** |
| Independently signed LOADED/owner proof | **NOT established** |

The tested physical output had `observed_at_ms=1791745652280`; this is an historical observation, not a live renewal receipt. No raw boot UUID, private endpoint, operator approval reference or private credential is published.

## Independent verification

On x1-370, a disposable downloaded exact-head research checkout passed:
- **12/12** fake-/proc tests (parser, model/hash mismatch, symlink, process restarts, ambiguous flag and no-authority).
- **35/35** signed-apply cryptographic/transaction negative tests (including boot identity and GGUF process-file binding).
- **8/8** real Neo4j 5.23 isolated disposable tests: persisted boot UUID/PID start ticks/file SHA match the signed source; approved stable-process continuation; restarted-process source with newly re-signed start ticks refused under the old runtime ID; original observation time custody; replay refusal, rollback; and **eight independent simultaneous two-worker canonical CAS races** each with only one commit.
- Strict Ruff PASS. Disposable Neo4j container used existing image, random loopback-only port, 1.7GB RAM and 1.25 CPUs, no NAS/prod volumes, automatic removal.

Earlier stacked [PR #307](https://github.com/scottjoyner/auto-assist/pull/307) independently enforces physical source observation ≤5 minutes and path canary ≤3 minutes. The boot/process source values do not change these short expiration windows or allow unattended renewal.

## Still blocked before production release

1. **Independent trusted source producer** running on a physical fleet node or separately attested host, including boot/process identity, loaded model proof, current capacity/leases and approved private-path completion; signers must have separate audited custody and revocation.
2. **Independent operator-reviewer signing authority**, new unique approval ID and exact signed source receipt review; no arbitrary locally minted keys accepted.
3. **DBA-reviewed unique constraints** for **all three** identity keys: `FleetProjectionState.name`, `RuntimeProjectionApprovalReceipt.approval_id`, and `RuntimeInstance.runtime_instance_id`. Read-only x1 audit found **all three missing**, though there is one canonical row, zero approval receipts, and **zero duplicate runtime IDs**. The draft apply refuses until all three exist. Proposed additional Neo4j 5 syntax (**DOCUMENTATION ONLY; NOT EXECUTED**):

   ```cypher
   CREATE CONSTRAINT unique_runtime_instance_id IF NOT EXISTS
   FOR (r:RuntimeInstance) REQUIRE r.runtime_instance_id IS UNIQUE;
   ```

   Review nullable properties, duplicate preflight, backups, ongoing writers, and rollback with a designated DBA before any production DDL.
4. **True host/process restart evidence:** draft transaction now refuses changed boot UUID/start ticks/model SHA for an existing runtime ID. Independent on-host signing, source/process→listener proof, and a reviewed new identity on legitimate restarts remain outstanding.
5. Production Neo4j staging rollback, source/original timestamp reader compatibility, retirement of legacy unsigned trust, Auto-Router authenticated owner receipts [#37](https://github.com/scottjoyner/auto-router/issues/37) and admin auth [#41](https://github.com/scottjoyner/auto-router/pull/41) / [AssistX #288](https://github.com/scottjoyner/auto-assist/pull/288).
6. Exact-build physical operator acceptance and separate explicit approval for any canonical generation beyond expired 643. **No reapproval or deployment attempted.**
