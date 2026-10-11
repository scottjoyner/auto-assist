# Physical LM Studio → GGUF worker lineage — October 11, 2026

**Status: verified UNSIGNED host-local evidence, code/tests in research draft. No keys, approvals, model autoload, Neo4j writes, routing or NAS changes.**

## Why the earlier evidence was insufficient

The prior approved Beelink GGUF worker `llama-server` was identified by PID **4783**, exact model hash and Linux boot/process start-time witness. Two network-path canaries had returned HTTP 200 and `PING_OK`. But the public LM Studio API endpoint and the GGUF worker **do not share a process/listening port**:

| Component | Physical /proc listener owner | Local port |
|---|---|---:|
| LM Studio gateway/API | `lm-studio` **PID 1535** | **1234** |
| Approved `liquid/lfm2.5-1.2b` worker | `llama-server` **PID 4783** | **45435** |
| Unrelated llama-server process | `llama-server` **PID 1006** | **1235** |

Actual physical `/proc/4783/stat` confirms parent PID **1535**, matching the API listener's process. Child `/health` returned 200 with `status=ok`; its `/v1/models` returned **401** requiring credentials. Existing LM Studio API `/v1/models` listed 11 candidate models while the direct worker's model route is protected. **No credentials were extracted or used, and no protected request was bypassed.**

This reconciles an important earlier ambiguity: the verified model process was NOT the owner of port 1234. A gateway health response + independently hashed GGUF does **not prove** that a particular LM Studio request reached the verified model process.

## New narrowly scoped collector

`scripts/collect_lmstudio_worker_lineage.py` uses only standard-library Python and the physical Linux host's procfs:

- Parse process identity and start ticks from `/proc/<pid>/stat` fields 3/4/22, and require `lm-studio` gateway and `llama-server` worker.
- Verify worker's **parent PID exactly equals gateway PID**.
- Reconcile `/proc/net/tcp` and `tcp6` **LISTEN** state socket inodes with the file-descriptor `socket:[inode]` symlinks owned by the two respective processes. Never infer ownership just from a port string or the existence of a listener.
- Require the gateway owns the approved local API port and the model worker owns a separate direct port.
- Send **GET /health** only to the worker's `127.0.0.1:<worker_port>`; refuse proxy and redirect, bound the response to 2048 bytes and 3-second timeout, and require JSON `{"status":"ok"}`.
- Recheck boot UUID, both start tick identities and both listening socket ownerships **after** the GET.
- Emit only sanitized `lmstudio-worker-lineage-observation-v1` fields (PIDs, local port numbers, boot UUID, start ticks and boolean flags); no private URL, raw cmdline, authorization header, model prompt or socket secret.

**Always false**: `gateway_forwarding_attested`, `model_loaded_inference_attested`, `source_signature_verified`, `owner_bound_request_attested`, `can_approve`, `can_dispatch`. This is an unsigned *observational link*, not an independently sourced worker execution receipt, independently verified model LOADED state, authorization or signer.

### Physical run via Fleet Commander

Fleet Commander streamed exact committed source through existing Tailscale SSH to the Beelink host as `python3 -`, without writing the script to Beelink. The observer returned success with:

- Gateway process: PID **1535**, start ticks **1125**, owned port **1234**.
- Expected GGUF worker: PID **4783**, start ticks **2340**, **parent 1535**, owned direct listening port **45435**.
- Same boot ID (published only as SHA-256 prefix **3bded808be0d**, not the raw boot UUID).
- Two listener socket inode ownership checks: **PASS**.
- Direct worker `GET /health`: **HTTP 200, status=ok**.
- **Request-level gateway → specific worker forwarding proof: NOT ESTABLISHED**.
- **Source or operator signature: NOT ESTABLISHED**.

Related independent physical source observation in [draft PR #309](https://github.com/scottjoyner/auto-assist/pull/309) verified the worker GGUF SHA256 `c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c` and 1,246,255,456 bytes. That prior unsigned witness was separate: the lineage collector itself does **not** rehash the model or prove resident model execution.

## Acceptance tests

`tests/test_lmstudio_worker_lineage.py` has **15** synthetic Linux /proc+TCP table cases, covering both owned listeners, wrong parent, wrong process, TCP connection mistaken for LISTEN, swapped inode, process restart, boot swap, health failure, alternate listener port, and no-authority fields. Standard-library only; no hosted CI listener/model required. Exact source-only local run: **15/15 tests PASS, Ruff PASS**.

## Next, before trusted source signing

1. Confirm model process and endpoint linkage using **authenticated, source-custodied execution evidence** that can actually bind gateway request ID → selected worker process/boot/start ticks → model artifact digest → completed response, without extracting credentials from the worker or installing surveillance tools. A merely observed parent/child relationship does NOT close this gate.
2. Establish independently authenticated host source producer identity and governed Ed25519 signing key with revocation/custody. A user-run script's JSON or arbitrary self-minted signer never qualifies.
3. Source receipt must include current and separately verified LOADED model status, capacity leases, approved LAN/Tailscale canaries, and full authorization ownership; separate operator reviewer signs the exact source receipt and canonical manifest.
4. Authorized DBA migration remains needed for **three** missing unique keys: `FleetProjectionState.name`, `RuntimeProjectionApprovalReceipt.approval_id`, `RuntimeInstance.runtime_instance_id`. No DDL executed.
5. Keep [draft PRs #303](https://github.com/scottjoyner/auto-assist/pull/303), [#305](https://github.com/scottjoyner/auto-assist/pull/305), [#307](https://github.com/scottjoyner/auto-assist/pull/307), [#309](https://github.com/scottjoyner/auto-assist/pull/309) and this new lineage branch draft-only. Auto-Router [P0 #52](https://github.com/scottjoyner/auto-router/issues/52) still tracks expired generation 643, no approved 644 or dispatch. Per-user [#37](https://github.com/scottjoyner/auto-router/issues/37) and authenticated admin rollout remain independently blocked.
