# Kipnerter mobile runtime recovery witness — 2026-10-10

**Disposition: NO-GO for Agent Auto admission. Read-only observation.**

Evidence was observed at 2026-10-10T15:39:16Z from x1-370. Source: scripts/mobile_recovery_witness.py. The SHA-256-attested JSON witness is docs/evidence/2026-10-10-mobile-recovery-witness.json. It contains no chat prompts, credentials, artifact model paths, user identity, or secrets. Its hash covers sorted compact JSON excluding record_sha256.

The script performs HTTP GET only; it denies redirects and refuses URL userinfo, query strings, fragments, and unsupported schemes. It cannot load models, approve a runtime, or change admission policy.

## Observations

| Evidence | Verified result |
|---|---|
| Tailnet gateway health | HTTP 200 |
| Tailnet mobile catalog | HTTP 200, agent_auto_available=false, 0 approved agent runtimes |
| x1-370 LM Studio | 24 advertised chat models, 0 resident |
| destroyer :1235 and :1236 | One resident llama.cpp model each, verified by health, props, and model alias |
| OptiPlex :1235 and :1236 | One resident llama.cpp model each, same witness |
| Canonical Neo4j generation | 642, expired 2026-10-07T22:42:17.527Z |
| RuntimeInstance | 20 total / 1 historically admitted / **0 fresh** |
| LoadedModelInstance | 44 total / 1 historically admitted / **0 fresh** |
| AccessPath | 35 total / 2 historically approved / **0 fresh** |
| CapacityObservation | 2,451 total / 1 historically approved / **0 fresh** |

The iPhone screenshot observed 2026-10-10 at 11:15 AM EDT showed a selectable x1-370 toolcall model but Agent Auto degraded. It establishes moment-in-time direct model discovery, not lasting model residency or agent authorization.

## Repeatable read-only check

Run the witness with provider arguments for x1-370:1234, destroyer:1235/1236, and scott-optiplex-9030-aio:1235/1236. For unattended gating use the --require-admitted flag: it exits code 2 when Agent Auto has zero currently approved runtimes. Acceptance: 16/16 tests passed in tests/test_mobile_recovery_witness.py.

## Required admission recovery

1. Obtain a **new attested runtime profile**, with current physical runtime and model artifact identity, fresh LAN and Tailnet path evidence, capacity/slot observations, and rollback canary.
2. Build a **non-mutating** candidate with scripts/build-runtime-projection-candidate.py using the fresh attested profile. Never synthesize an approval identity or waive dual-path requirements to satisfy the tool.
3. Have the authorized operator review new generation/revision, checksum, evidence provenance, expiration, and admission lease.
4. Only after that approval, apply the signed canonical projection using scripts/approve-runtime-projection.py --apply and independently verify the AssistX and auto-router generation, signature, freshness, rollback, and slot admission.
5. Validate a signed Kipnerter iOS build: discovery of four persistent fleet models, a full direct chat turn with trace history, and Agent Auto only after it becomes genuinely admitted.

**No production admission updates, generation bumps, signing changes, model loads, or device deployments were performed by this witness.** A healthy gateway and a loaded model do not imply agent-routing authority.

Related reviews: scottjoyner/kipnerter-ios#362 and #375; scottjoyner/auto-assist#254 (handler fix already present in live container); superseded duplicate #257 closed. The main AssistX PR CI remains red from broad unrelated failures and Kipnerter's CI scope classifier is red, despite narrow tests passing. Full release acceptance is not complete.

## Follow-up live witness after bounded direct-model loads

At 2026-10-10T15:45:53Z, the same read-only witness confirmed two resident x1-370-advertised LM Studio chat models, plus the original four llama.cpp models; the signed AssistX catalog still reported zero admitted runtimes. The new immutable evidence is docs/evidence/2026-10-10-mobile-recovery-witness-post-load.json (SHA-256 c74af43583145e6f231652730314a4f2f0e01ae417ec60e615d23b3e72e61b7d).

The two direct LM Studio model loads were TTL-bounded, not permanent preload policy. A benign nonstreaming MiniCPM5-2B request returned the requested READY (HTTP 200, 0.36s); streaming finished [DONE] after 26 SSE data frames (HTTP 200, 0.62s) with Ready!. The other loaded model emitted stray tool markup during a smoke test; its transport passed but its output-quality acceptance failed. No complete physical-iPhone turn, prompt-ledger persistence, or Agent Auto admission was proven.
