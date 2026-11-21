# Mercury Agent — physical dual-node authority and copied-state failure witness

**Executed:** Friday, October 9, 2026, ~16:25–16:39 EDT (20:25–20:39 UTC).
**Actual machines:** `x1-370` as original authority and first client; `xwing` as second client and later separate copied authority.
**Network:** direct TCP over host Tailscale addresses. Local SSH from x1-370 to xwing was used to stage the disposable fixture and invoke the remote client, but signed client messages went over HTTP on Tailscale interfaces.
**Scope:** research-only disposable Python 3 stdlib probe. **Not the production Mercury v4 TypeScript coordinator**, not a fleet service deployment, not actual LLM generation or provider quota authority. No provider credentials or NAS data were read or changed.
**Continuity:** [auto-assist Mercury research PR #232](https://github.com/scottjoyner/auto-assist/pull/232) already established 813/813 pinned Mercury tests and a signed *in-process* mock. This experiment adds real two-host network and SQLite recovery evidence, not a drop-in v5 Mercury integration.

## Physical network and admission acceptance

The final validated fixture binds its exact allowlist to `nodeId / botId / provider / model / upstreamGroup` and enforces test-only token ceilings. Each node had a temporary 32-byte HMAC test key; requests and responses included authenticated nonces. All keys were destroyed after the experiment.

| UTC, Oct 9 | Physical trial | Observed |
| --- | --- | --- |
| **20:34:33** | `x1-370` acquires at authority epoch **1** | **Admitted**, audit head `ad2b42229a3c712e0c177b95525067af71286c2be1882099a2d9aaeb9eacc606` |
| **20:34:33** | `xwing` attempts same upstream slot | **Denied**, `occupied_or_stale` |
| **20:34:33** | `xwing` sends unauthorised bot `intruder` | **Denied**, `policy_denied` |
| **20:34:33** | `x1-370` releases, `xwing` acquires | **Released → admitted**, then release succeeds |
| **20:35:06** | `x1-370` admits a lease then its authority process is stopped | Local renewal and remote acquire both **unavailable/denied** while server down |
| **20:35:07** | Same SQLite DB reopened; epoch advances **1 → 2** | Old slot held through its original expiry; new request denied; prior-epoch renewal denied |
| **20:35:26** | After prior TTL, `xwing` acquires and releases | **Admitted → released**; DB recovery permits progress only after the old seat expires |
| **20:37:06** | Fresh SQLite backup copied to `xwing`; independent authority starts at epoch **3** while original stays at **2** | **Both admit conflicting requests** for the same upstream provider group. Physical split-brain counterexample confirmed |
| **20:39:07** | Both audit SQLite logs inspected after test listeners stopped | Local hash-chain validation **PASS**; both experimental TCP ports no longer listening |
| **20:39:26** | Disposable fixture configuration and HMAC secrets removed from both machines | No test keys committed |

These times are derived from commands run during the session; individual signed decisions contain their own millisecond UTC timestamp in the exported audit rows.

## Proof of failure — two independent authorities

After the original epoch-2 database had no unexpired lease, a SQLite backup was copied to physical `xwing`. Starting the copied authority advanced *its own* epoch to 3, but did not invalidate or fence original `x1-370` epoch 2. The original admitted `final-fork-task-a` while the copied authority admitted `final-fork-task-b`. Both signed replies were verified by their clients.

**Exactly what this disproves:** HMAC authentication, local file locking, SQLite durability, restart epoch increment and per-authority slot capacity **cannot prevent independent authority copies from making overlapping decisions**. This is the same class of physical negative previously reproduced in fleet admission work; no real provider work was submitted in this experiment.

**What it does not establish:** production-safe takeover, quorum/consensus, fencing against a still-running provider request, vendor allowance/credits, network confidentiality (transport is **plaintext HTTP** with throwaway test HMAC keys), billing, physical credential isolation or independently witnessed tamper-proof audit. No claims of safe distributed failover or production admission are made.

## Recorded, non-secret trace evidence

Two versioned JSONL extracts contain **only fixture decision fields** and `sequence / prev_hash / hash` per event. `export_physical_audit.py` revalidated every SHA-256 link against the source SQLite state before export.

| Host/authority | Events | Last audit digest | SHA-256 of JSONL |
| --- | --- | --- | --- |
| `x1-370`, epoch 2 | **14** | `79b8506ef6d0327807f6b81bca392501ce9bcc36fba4437a8950c718e1b103e1` | `ab927e72cae11d95f55b61a8a09f8741cd5a16d40558e9969c4c2a1b29e7f412` |
| `xwing`, copied authority epoch 3 | **15** | `47ad9b0e5f236c6691b41be320f65cc0ab0baa68d73e43a1d79d0591f74679d7` | `79d8d23ee94fee627a8c130e75e82cf6eb2754b65d5b0c8b54451e6f6f24983d` |

Evidence files: `docs/evidence/mercury-x1-370-authority-events-20261009.jsonl` and `docs/evidence/mercury-xwing-copied-authority-events-20261009.jsonl`. These are an auditable snapshot in Git, **not independently secured WORM storage**. A writer with direct SQLite access could rewrite the unauthenticated local hash chain; do not compact/discard original source traces based on this demonstration.

## Code and reproducibility

- `integrations/mercury/research/physical_lease_probe.py`: separate disposable HMAC-signed HTTP endpoint, single SQLite lease table, per-file OS `flock`, epoch rollover, persistent nonces and append-only *application-level* hash-linked audit table. The original authority's active row is held until TTL after restart.
- `integrations/mercury/research/test_physical_lease_probe.py`: **7/7** stdlib unit tests passed on `x1-370`, including model/bot/upstream denial, HMAC tampering, replay, exclusive file locking, restart epoch denial, and audit corruption detection.
- `integrations/mercury/research/export_physical_audit.py`: whitelisted synthetic event export with chain validation.
- The final service listeners were stopped and ports **18743** on `x1-370` and **18744** on `xwing` verified closed. Temporary key/configuration files removed. Disposable SQLite stores remain under private `/tmp/mercury-phys-20261009-tPb19x` directories on their respective hosts for immediate local investigation; they are **not** permanent NAS custody.

Run offline unit tests via:

```bash
cd integrations/mercury/research
python3 -m unittest -v test_physical_lease_probe.py
# 7 tests, no network, no Mercury daemon, no hosted provider
```

For another physical acceptance, generate **new** test-only keys, choose unused private IP bind addresses and ephemeral ports, keep server and SQLite within freshly created restricted scratch dirs, and issue `physical_lease_probe.py serve/call/inspect` commands on separately reachable nodes. Only run while a human controls both machines. Never reuse the ephemeral deleted fixture secrets or let this research listener access production provider credentials.

## Gate/decision and follow-up

**Decision:** treat this as a **successful negative acceptance** for physical split brain, not as successful distributed failover. Keep [Mercury PR #232](https://github.com/scottjoyner/auto-assist/pull/232) and this research continuation draft. Do not enable the Mercury fleet flag or start live free-provider/paid-provider inference. Free-token custody checkpoint remains `CHECKPOINT_NOT_REACHED`.

**Smallest next engineering gate:** add a durable *external* leader/epoch fencing source that is not copyable with the authority database, prove the stale authority loses its credential/transport capability before any other authority can admit, test physical partition/takeover with independent witnesses and application-layer cancellation, and attach signed receipts to independently restorable storage. A plain local SQLite lock, locally incremented epoch and hash chain are insufficient.
