# K2: available model inventory vs actual loaded instances

November 3, 2026 ~08:20–08:24 EST. Read-only x1-370 check.
Research draft; canonical production admission remains HOLD.

## Confirmed live facts

- Installed LM Studio version (package metadata): 0.4.28+2.
  Reviewed K2 runtime pin remains 0.4.26+4. Do NOT silently
  approve the installed version or claim that the running
  executable was physically measured.
- Installed LM Studio executable SHA-256 was independently
  recomputed on disk (stat unchanged throughout):
  9d753d6e324389ed449234b88fce7157a34627ce5abcd8cf2d5feabd14a83616
  The running /proc PID/exe remains inaccessible from Fleet Commander's
  restricted user namespace; this disk digest is NOT runtime proof.
- The on-disk K2 Horizon BF16 GGUF (2,159,424,896 bytes) was
  freshly hashed with no stat change during hashing. SHA-256:
  371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8
  The exact previously reviewed GGUF bytes are present on disk.
  That alone never proves a loaded model or generation capacity.
- LM Studio native GET /api/v1/models returned 11 available entries:
  10 LLMs, one embedding model, four K2 Horizon architecture variants.
  Every entry's loaded_instances list was EMPTY (zero total).
- All three existing private OpenAI-compatible GET /v1/models
  paths — loopback, LAN, Tailscale — returned HTTP 200 and
  ZERO advertised model IDs. None exposed k2-horizon-0.9b.
- The existing five-minute lm-studio-api-recheck.timer is ACTIVE.
  The unit description expressly says "API availability check
  (no model loading)"; recent service journals report
  API_WATCHDOG: healthy. Its latest run was successful.
  This confirms only API reachability and not model residency,
  token throughput, actual inference, or K2 availability.
- Auto-Router remains degraded, no enabled free hosted providers,
  and the canonical admission read-only check remains expired
  generation 896, gate HOLD, can_dispatch=false.

Independent primary reference for native LM Studio REST v1:
https://lmstudio.ai/docs/developer/rest/list
It defines GET /api/v1/models as the available model list
and loaded_instances as the currently loaded instances of each model.
The POST /api/v1/models/load operation is NOT used by this work.

## New passive readiness diagnostic

    python3 -B scripts/inspect-k2-catalog-residency-readonly.py

It makes exactly four fixed bounded, no-proxy, no-redirect HTTP GETs
(native loopback and three exact existing private paths).
It refuses unapproved URLs, non-200/oversized/invalid JSON, duplicate
keys, model/instance duplicate IDs and malformed inventory records.
It only returns aggregate counts and booleans, never model filesystem
keys, API secrets, underlying response bodies, IPs, file paths or
exception text.

On Nov 3, the result was:
- all_catalogs_reachable=true
- native.available_model_entries=11
- native.available_k2_family_entries=4
- native.loaded_instance_count=0
- native.loaded_k2_instance_count=0
- every private path: loaded_model_ids=0
- status=CATALOG_HEALTHY_ALL_MODELS_IDLE

States including CATALOG_HEALTHY_ALL_MODELS_IDLE,
EXPECTED_K2_NOT_RESIDENT, INCONSISTENT_RESIDENCY_CATALOGS_HOLD,
CATALOG_UNAVAILABLE_HOLD and
CATALOGS_INDICATE_K2_LOAD_NOT_PHYSICAL_PROOF distinguish observable
API claims. Even the last state is NOT actual physical attestation,
healthy generation, independent custody or graph admission.

This CLI ALWAYS exits 2 and reports
physical_model_residency_verified=false,
actual_generation_tokens_measured=false,
canonical_operator_approval_verified=false,
independent_witness_verified=false, can_dispatch=false,
decision=HOLD. No model is loaded or unloaded; no endpoint
is called using POST. A downloaded model is NOT promoted.

## Operational next steps requiring separate gates

1. Retain the existing API watchdog as a narrow API-liveness check,
   but do not use its health status as a residency or token-
   utilization metric. A separate read-only monitor can consume the
   new aggregate inventory; do NOT change the installed timer in
   this draft research work.
2. An authorized runtime owner must review/qualify installed
   LM Studio 0.4.28+2 against the pinned 0.4.26+4 and the intended
   64k/4-slot workload in a disposable, resource-bounded isolated
   environment. No production listener model load is authorized.
3. An independent observer must attest the actual executable,
   process/socket owners, mapped GGUF, boot/process identity and
   loaded catalog on all paths under separate operator custody.
   The Fleet Commander /proc security boundary is not bypassed.
4. An independently administered issuer and high-water anchor must
   verify single use and rollback resistance, then an explicitly
   approved fresh signed canonical CAS would be required.
5. Only subsequently evaluate controlled production inference and
   independently measured prompt/completion tokens across providers.

No production model, service, authorization, key, repository merge,
private trace, graph record, host privilege, or routing setting
was changed to gather this evidence.
