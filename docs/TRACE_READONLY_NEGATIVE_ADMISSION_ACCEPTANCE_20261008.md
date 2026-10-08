# Two-node lease negative admission: read-only fixture acceptance

**Decision date:** 2026-10-08 America/New_York.
**Parent:** [draft AssistX PR #119](https://github.com/scottjoyner/auto-assist/pull/119).
**Production authority:** [issue #120](https://github.com/scottjoyner/auto-assist/issues/120).

## Problem and prospective prediction

The original offline smoke built a synthetic Task with a legacy task_type marker but without the current canonical Neo4j Task fields kind and ticket_type. The hardened issuer requires BOTH canonical markers. The smoke was therefore at risk of failing before it exercised any verifier negative case. This patch aligns the fixture with the real schema without constructing a real Task or contacting any service.

**Prospective prediction for an independent physical replay:** On xwing and scotts-macbook-air, the newly staged read-only script should verify a short Ed25519 lease together with a fresh challenge-bound status proof, then deny ten invalid cases: missing current status, wrong challenge, wrong node, expired status, modified lease, wrong signer, cancellation before status signing, supersession before status signing, changed generation before status signing, and wrong claim-owner node. Results should report 0 executed commands, 0 persisted tasks, and 0 journal mutations. This prediction concerns future physical shadow replay; local CI success alone is not evidence of physical admission.

## Exact isolated commands

Run only from a current, reviewed checkout of the trace PR or a shadow-only checkout with its Python dependencies, on the authorized host. Set PYTHONDONTWRITEBYTECODE to avoid import-cache mutation. Do not restart a worker, register a capability, provision keys or connect to production.

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 scripts/trace_claim_lease_platform_smoke.py --node-id xwing

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 scripts/trace_claim_lease_platform_smoke.py --node-id scotts-macbook-air

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q tests/test_trace_claim_lease_platform_smoke.py

Use the first command only on xwing and the second only on the Mac. Running the Mac identity fixture on a different host is a code portability check, not physical-host acceptance.

## Evidence ledger contract

For each actual host replay, capture the host identity from independently authenticated fleet inventory, UTC execution timestamp, Python runtime version, exact deployed shadow commit, script SHA-256, process exit code, JSON output plus its SHA-256, and a signed custody receipt outside the node if available. Verify that stdout declares mode=in-memory-claim-fixture-only, fixture_cases=10, executed_commands=0, persisted_test_tasks=0, journal_mutations=0 and all four production authority fields false. Do not record tokens, private keys, private file contents or privileged route URLs.

**Do not misclassify:** The reported zeroes are the script's structural scope, not a measurement of every concurrent process on a live host. No script output can prove instantaneous revocation or physical authenticated negative admission against a deployed issuer.

## Remaining gates, unchanged

| Boundary | Fixture acceptance | Production prerequisite still missing |
| --- | --- | --- |
| Lease freshness | Short signed lease and nonce-bound status | Genuine committed AssistX claim, live node-authorized issuer |
| Revocation and supersession | Reject changed fixture before renewed status | Distributed monotonic fencing; cancel after issue and stale-proof denial |
| Wrong node / replay | Reject invalid fixture signature, node and challenge | Authenticated physical node credentials and real issuer denial |
| Production API startup | Not tested | Production API imports, routes, TLS and disabled-state smoke |
| Signing-key custody | Ephemeral signer only | Protected issuer key, separated public key pins, escrow/rotation |
| Physical negative admission | Portable fixture checks only | Real node-specific negative acceptance with no unauthorized commands |
| Audit / NAS | No journal/NAS touch | Real private CIFS restore, WORM witness and full CI |

**Hold:** PR #119 remains draft; production lease issuer, trace worker and unsafe shell remain disabled. The script never calls a real API, acquires a Neo4j claim or writes journal/NAS data. Successful fixture checks cannot authorize deployment, provider inference, shell, script dispatch, or any general execution.


## Observed independent physical fixture replay — 2026-10-08 UTC

**Prediction recorded above before the host replay.** After CI, the controller used BatchMode SSH with StrictHostKeyChecking to stream the proposed smoke script through standard input to each existing physical shadow interpreter. No source files were installed or written on either node. Neither worker was restarted; no real AssistX task, node token, live issuer, NAS write or graph access occurred. Python bytecode writes were disabled.

**Source lineage**
- Proposed smoke script Git blob SHA: da08ca8bf8e75c04d240de6726bb9ba2f06eeb0a; exact UTF-8 SHA-256: 3d014f4cfc432fd2681518e058a58a21256210d38fec540421a12299c9587011.
- Existing shadow lease verifier SHA-256 on **both** hosts: 04aa91e4db0de64d3889173cd96de505da0ad21297eab1df72ec6dcd1dc6ebaf, matching the GitHub version of src/assistx/trace_claim_lease.py at this PR head.
- Controller proposed source head during host replay: c088249e12010423b5d41557c07fde7954ef8165 (NOT a production deployment).

| Target identity | SSH hostname | UTC | Python | Denials | Journal rows before/after | Journal SHA-256 before and after |
| --- | --- | --- | --- | --- | --- | --- |
| xwing | xwing | 2026-10-08T21:51:24Z | 3.12.3 | 10/10 | 12 → 12 | a38acdafdf852db1ae8f795d45050bec3075bfa7ac103c093318ddf0f0bf6b72 |
| scotts-macbook-air | kipnerter | 2026-10-08T21:51:28Z | 3.9.6 | 10/10 | 10 → 10 | 56b927cbe65963bbd84a4b430081e2a9b8f64ee4d53cf9c28bd64d3bea4dcf88 |

Each returned ok=true, mode=in-memory-claim-fixture-only, fixture_cases=10, executed_commands=0, persisted_test_tasks=0, journal_mutations=0, assistx_claim_verified=false, production_issuer_verified=false, physical_authenticated_negative_admission=false, and production_dispatch_authorized=false. Both returned the same ten labels: missing_status, wrong_challenge, wrong_node, expired_status, tampered_lease, wrong_signer, cancelled_before_status, superseded_before_status, generation_changed_before_status, wrong_node_claim.

**CI observation:** GitHub dedicated trace run 37845988608 (job 113546872434) completed SUCCESS: 198 passed, 2 warnings. Broad CI run 37845988633 FAILED: 851 passed, 77 failed, 59 deselected; recovery-canary: 11 passed, 1 failed (missing RepositorySourceBinding.from_contract_payload). Broad CI is not waived.

### Interpretation and remaining blockers

The script genuinely executed its fixture-only verification on both distinct SSH-authenticated physical hosts. It is not evidence for a production-issued committed claim or authenticated remote negative admission. The signed current-status fixture cannot establish instantaneous revocation, especially for cancellation occurring after signing. The journal hashes independently prove the targeted existing journal was not changed during each replay; script zeroes by themselves would not prove global fleet inactivity. No independently durable signed custody receipt for this new experiment was issued; this GitHub record holds the observation until external custody exists.

**Decision:** read-only physical fixture portability is EVIDENCED. Deployed issuer/startup, protected production keys and escrow, authenticated physical negative admission, distributed revocation/fencing, independent NAS WORM custody and broad CI remain BLOCKED. Keep both PR #119 and PR #132 draft.
