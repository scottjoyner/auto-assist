# LM Studio request→worker receipt contract — offline research (2026-10-11)

**Status: DRAFT PROTOCOL, synthetic signed test data only. Not an operational worker attestation, provider integration, loaded-model proof, owner execution receipt, canonical approval, or release.**

Tracks [AssistX P0 issue #313](https://github.com/scottjoyner/auto-assist/issues/313) and stacks on [physical lineage PR #311](https://github.com/scottjoyner/auto-assist/pull/311), which stacks on source/process identity [#309](https://github.com/scottjoyner/auto-assist/pull/309), signed approval candidate [#305](https://github.com/scottjoyner/auto-assist/pull/305), and observation freshness reader [#307](https://github.com/scottjoyner/auto-assist/pull/307).

## Confirmed external/provider limitation

LM Studio documents its native `POST /api/v1/chat` and OpenAI-compatible `/v1/chat/completions` chat APIs, plus `GET /api/v1/models` and `GET /api/v0/models`. Native chat can provide a `response_id`; model listing can show LOADED states. Those are **not** signed gateway-to-specific-physical-worker execution receipts. See [official API overview](https://lmstudio.ai/docs/developer/rest), [native chat docs](https://lmstudio.ai/docs/developer/rest/chat), [headless/JIT-loading docs](https://lmstudio.ai/docs/developer/core/headless).

Physical Beelink inspection proved **LM Studio PID 1535** owns gateway :1234; exact GGUF worker **llama-server PID 4783** is its child, starts at boot-relative ticks 2340, owns direct listener :45435; a different llama-server PID 1006 owns :1235. A direct worker GET /health passed, but direct GET /v1/models returned HTTP 401 and was left untouched. LM Studio's `/api/v1/models` `loaded_instances` items contained only `id` and `config`, **not worker PID or boot/process start identity**. The historical gateway `PING_OK` canaries and exact worker GGUF SHA cannot prove a particular gateway response originated at that worker.

**Never send unadmitted inference just to discover a PID:** LM Studio's headless options may JIT-load a model on inference, changing memory and hardware state. Do not acquire or bypass worker API credentials, instrument live prompts, or widen listeners without separate operator approval.

## New standalone validator — NO provider calls

`scripts/verify_lmstudio_worker_request_pair.py` is a Python-only **offline protocol contract** with `cryptography` Ed25519 verifier; it never signs, imports model runtimes, contacts an API, opens credentials or touches Neo4j.

It checks two separately Ed25519-signed canonical JSON payloads against **two separately supplied public keys**:

**Worker receipt (must be issued and signed by a genuinely trusted physical worker):** same opaque `request_id`, keyed pseudonymous `owner_digest` (the real gateway must derive this from authenticated ownership), `request_sha256`, replay-resistant `nonce_sha256`, fixed role/audience, boot UUID, worker PID/start ticks, gateway PID/start ticks, exact model GGUF SHA256, explicit gateway/worker ports, start/completed times, terminal `COMPLETED` outcome, finish_reason=`stop`, exact `output_sha256`, and completion token count. It must establish actual worker inference before signing; **this is not available from the current unsigned host observer.**

**Gateway receipt (must be issued and signed by the independently trusted authenticated ingress gateway):** same request/owner/nonce/output/model/worker identity, gateway start/completed timestamps, HTTP 200 and usable response ID, terminal `COMPLETED`, and the SHA256 of the **entire originally signed worker receipt**. The gateway must not synthesize a worker receipt. The worker/response digest agreement implies both trusted producers share a defined output-canonicalization convention, which **must be designed and physically tested** before integrating real LM Studio outputs.

The validator rejects missing or unverifiable signatures, same-key claimed independence, altered worker signatures, role/audience confusion, unknown/raw prompt fields, impossible/future/stale timestamps, negative/boolean or oversized token counts, wrong model/process/boot/ports, swapped output and owner/request hashes, different response IDs or nonterminal outcomes. It enforces complete output and bounded duration (≤90s), signed receipt age (≤180s), bounded clock skew (≤5s).

It optionally rejects `already_seen_request_ids`, **but DOES NOT provide atomic durable replay protection**. Production needs a centrally governed, authenticated request/nonce ledger, atomic claim/revocation and concurrency testing. A local in-memory set, request UUID or matching hash alone is **not** an ownership proof.

### Mandatory non-authority boundary

Even a mathematically valid pair from two **ephemeral locally minted test keys** produces only a `Pair` review structure and a sanitized report with:
- `status=CRYPTOGRAPHIC_PAIR_CONSISTENT_ONLY`
- `producer_key_custody_established=false`
- `independent_worker_execution_attested=false`
- `trusted_owner_binding_established=false`
- `signed_operator_approval=false`
- `can_renew=false`, `can_dispatch=false`

**Never promote this output into source-authoritative execution simply because both signature verifications pass.** A real worker could be spoofed by arbitrary signers if the public keys were not independently provisioned, authenticated and revoked. Nor does output match prove that the model weights actually ran without binding the signer to the physical process and request.

## Test evidence

- New `tests/test_verify_lmstudio_worker_request_pair.py`: **34 ephemeral-key, mock-only cases PASS**. Includes two-user separation, swapped worker/GGUF, boot/process restart, incomplete/timeout/truncation, output mismatch, wrong nonce, stale/future interval, pre-existing request ID, same key, attacker-generated key and raw prompt field rejection.
- Inherited `tests/test_lmstudio_worker_lineage.py`: **15 PASS**.
- Inherited `tests/test_correlate_unsigned_host_lineage.py`: **17 PASS**.
- Exact GitHub-sourced files verified on Fleet Commander x1-370 in isolated /tmp directory: **66/66 local tests PASS, Ruff clean** at commit `4567596c7b35b297ab06bda5b840626a13a29783`.
- The hosted branch-scoped CI validates these 66 tests (and source-only non-authority flags). **Neither hosted tests nor local synthetic signing constitute physical execution.**

## Smallest safe next producer slice and release gates

1. Identify a **documented, sanctioned LM Studio per-request worker provenance API/hook** or a separately governed external worker instrumentation design. Current documented chat and model-list APIs do not supply the signed physical request-worker mapping. A read-only capability inventory comes first; no LM Studio binary patch, private memory instrumentation, packet capture, process injection or stealth log collection.
2. Define host-rooted signer identity/key custody (physical attestation/TPM/managed service as appropriate), its issuer, rotation/revocation and boot/process attestation. Independently authorize the gateway signer and its request owner authentication. Merely generating two keys on one host fails independence.
3. Choose exactly one already-loaded and resource-admitted model; execute a **single operator-authorized bounded canary** with a unique client request ID and trusted worker completion witness. Negative tests: model switch, worker restart between dispatch/return, output digest mismatch, truncated stream, source signer revocation, expired permissions, concurrent two-user owner separation.
4. Establish authenticated source custody and atomic replay ledger, then review the signed worker+gateway contract against the per-user Auto-Router [issue #37](https://github.com/scottjoyner/auto-router/issues/37). Keep independent operator approval separate.
5. Only after above, stage three authorized Neo4j uniqueness constraints (`FleetProjectionState.name`, `RuntimeProjectionApprovalReceipt.approval_id`, `RuntimeInstance.runtime_instance_id`), source-time reader expiry, AssistX/Router exact-generation compatibility, legacy unsigned retirement, and authenticated admin rollout. **Do not apply or renew canonical generation 643/644 from this experiment.**

**NO production inference, model autoload, signing key provisioning, network-port change, runtime approval, Neo4j DDL, NAS operation or dispatch was performed.**
