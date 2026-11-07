# Signed Docker-private LFM3 canary — real physical inference proof

**2026-11-07 ~18:25 EST — read-only production state preserved; Task admission HOLD.**

This work does **not** install or deploy a permanent inference gateway,
register a fleet principal, create a Worker Task, modify Neo4j or signed
WorkSupplyState, change the existing `llama-server` bind addresses or provide
operational authority for arbitrary prompts. It proves one bounded, signed
inference request across the otherwise isolated Docker/host namespace.

## Background

- Production `assistx-api` lives in the Docker-private bridge
  `home_scott_git_auto-assist_default`; currently its client source IP is
  `172.21.0.6` and bridge gateway `172.21.0.1`.
- Existing `x1-370` LFM3-1.7B service `lfm3-1.7b-x1-fast` listens only on
  `127.0.0.1:19181`, process residency and 1,099,627,808-byte GGUF SHA-256
  `3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4`.
  No TCP listener exists on LAN/Tailscale or Docker gateway for its inference.
- Two previous three-way short canaries (3 and 32 output tokens) established
  local host capability, but container-to-host inference remained unreachable.

## Minimal signed one-shot implementation

`scripts/lfm3_signed_private_canary.py` is an unprivileged, ephemeral HTTP
listener strictly bound to the verified private Docker bridge address. It
checks the expected source IP and verifies that it is in the actual bridge
subnet. It rejects non-bridge/loopback/public binding, rejects root, and
closes after at most four requests or 20 seconds by default. The 32-byte
random HMAC key is consumed once from an inherited anonymous file descriptor,
never an environment variable, argument, file or log. The client receives the
same ephemeral key only on its process standard input, never a task token or
operator Basic credential. No bearer or long-lived authentication key is
provisioned.

The only executable API is **POST `/canary`** with an HMAC-SHA256 signature
covering method, exact path, 10-second timestamp, 128-bit random nonce and
SHA-256 digest of a bounded request body. Invalid signatures, expired dates,
malformed headers, duplicate JSON fields, larger payloads, boolean-as-integer type substitution and repeated nonce
are rejected without calling the model. The JSON request must equal the fixed
synthetic four-output-token prompt `Reply OK.` for the exact LFM3 alias;
streaming, arbitrary model IDs, different prompt content and higher budgets
are not exposed. The server verifies upstream health, one correct model alias
and exactly one currently idle slot before sending that single fixed canary
request to **host loopback only**. The response is a sanitized contract and
completion-token count; model-generated text never leaves the host helper.
Every response retains `task_admission=HOLD` and zero approved resource slots.

`scripts/lfm3_signed_canary_live_witness.py` is the operator-controlled
bounded tester. It dynamically checks the existing Docker container and
bridge identity, requires exact expected client/gateway addresses and an idle
LFM3 slot, creates one random key in memory and launches the helper using an
anonymous pipe. It makes one refused request from the wrong host source, then
runs a fixed `docker exec -i` client whose key also arrives over stdin. That
client submits an invalid signature, a valid signed request and a replay of
the same signed request. No secret appears in the process argument list,
environment, disk or output. The process then exits status **2/HOLD**, and
its temporary listener is independently checked absent.

## Real production-container client result

| Exact source and operation | Live HTTP |
|---|---|
| Wrong host source, POST `/canary` | **403** |
| Real `assistx-api` Docker client, invalid HMAC | **403** |
| Same client, valid signed fixed LFM3 completion | **200** |
| Same client, exact replay of signed request | **409** |
| Helper exit after four attempts | **exit 2, CLOSED, no listener** |

The valid 200 performed **one actual inference** against existing LFM3
`127.0.0.1:19181`, with 1–4 output tokens. No prompt or completion text was
logged or surfaced, no Task was claimed and the API container stayed healthy.
Other unrelated routes, methods and model identities cannot be forwarded.

Offline security tests (**28/28 at final exact source**) cover HMAC canonicalization,
body tampering, expiry and future-date rejection, replay, source-IP
allowlisting, real in-memory HTTP 403/409, fixed model/slot validation,
upstream token-budget checks, oversized response, duplicate JSON fields and
legacy Basic refusal. The dedicated least-privilege hosted workflow runs only
synthetic tests (no fleet credentials or physical model calls); production
source has not been changed by this PR.

## Release decision — HOLD

A one-shot synthetic-key canary proves protected *connectivity*, **not** a
production-authenticated user/workflow transport. A durable gateway would
need a separate production principal/credential manager with rotation and
mutual transport/caller verification, privacy/prompt controls, strict
per-model admission and resource accounting, incident rollback, observability
and independent security review. The signed canonical runtime projection
still references expired generation 896, and fresh approved runtime,
model, access path, capacity and WorkSupplyState records are all absent.
Until a designated owner signs fresh physically justified authority,
resource-owned CAS/COMMIT is verified and one supervised Task is released,
production **Task admission must remain HOLD**.
