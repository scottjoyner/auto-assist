# Fleet worker: native resident chat-model contract (source-only)

## Why

A live LM Studio `/v1/models` list may contain many cataloged/downloaded
models even though only a few native `loaded_instances` exist. The original
fleet worker upgraded any nonempty compatibility catalog to `llm` capability
and could call a model absent from that node. This can fabricate productive
capacity, select an embedding model for chat, or cause implicit model loading
under the wrong GPU ownership/VRAM assumptions.

## Fail-closed source behavior

The new worker uses read-only `GET /api/v1/models`, requiring an object with a
`models` array and selecting only entries whose `type == "llm"`, nonblank `key`
and nonempty native `loaded_instances` list with object members. It advertises
only these resident chat-model IDs with `llm` and `lmstudio` capabilities.
Extra `FLEET_CAPABILITIES=llm,lmstudio` cannot override the negative native
observation. If the native API is unavailable, an OpenAI-compatible catalog
on `/v1/models` is **not** proof of residency; other runtimes require a
separately approved adapter/identity attestation, not an automatic fallback.

The worker validates the requested model from the authoritative *claimed Task*
against freshly re-read native resident IDs before sending a generation
request (including benchmark tasks). A model omitted by the claim may default
only when there is exactly **one** resident chat model. Multiple resident
models with an unspecified request fail closed. No model is loaded, evicted,
or altered, and no token, signed projection, Task record or GPU lease is
mutated by the discovery calls. After a valid claim with no matching resident
model, the existing claim-fenced Task-completion path reports `FAILED`, not
`DONE`; it does not attempt an inference or silently switch models.

## Evidence

- Broad local fleet-node suite: **90 passed**, one preexisting Pydantic
  namespace warning. Tests include native-vs-catalog distinction, embedding-
  only, malformed data, explicit model mismatch, unique-model selection,
  missing model, unpinned/multi-model rejection, benchmark rejection and
  existing claim/heartbeat/completion/auth/recovery regressions.
- Live read-only source function on **xwing** returned exactly resident chat
  `ornith-1.5-35b-a3b-apex-mtp`; on **Beelink**, exactly
  `liquid/lfm2.5-1.2b`; default **Deathstar** :1234 returned no chat models.
  This intentionally excludes xwing's loaded Nomic embedding model and all
  catalog-only entries.

## Release safety

This branch stacks on draft AssistX [PR #482](https://github.com/scottjoyner/auto-assist/pull/482).
It does **not** establish physically signed runtime admission, exact weight
artifact identity, independently owned model receipt, Task claim/lease
fencing, or valid production credentials. Five observed remote agent services
still source old worker code and return HTTP 401 when the existing credential
file values are read by the [PR #490](https://github.com/scottjoyner/auto-assist/pull/490)
preflight. The canonical work-supply projection remains expired, and no
production slots are authorized. Deployment is **NO-GO** pending approved
single-lane release, credential owner reconciliation, server P0 #428
transactional fencing and signed exact-model evidence. Source-only tests and
read-only native inspection do not change that decision.