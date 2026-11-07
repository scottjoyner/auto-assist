# LFM3 Docker-private connectivity and bounded physical capacity witness

**2026-11-07 on x1-370. Research PASS / production Task admission HOLD.**

This is a deliberately **ephemeral metadata-only** witness, not a permanent
model endpoint, authenticated inference service, shared capacity allocation,
signed projection, nor production dispatch release. Running it does not add a
new model, load/unload an existing model, touch graph/Task rows, read secrets,
restart agents or expose model generation on a Docker/LAN/Tailnet interface.

## Real physical evidence

- Two existing `llama-server` instances bound **only** to host loopback:
  - `127.0.0.1:19180`: model alias `lfm3-local-test`, configured context 4096,
    four slot entries; **4/4 idle** immediately before test.
  - `127.0.0.1:19181`: alias `lfm3-1.7b-x1-fast`, context 2048,
    `--parallel 1`, one slot entry; **1/1 idle** before test.
- Both processes reference the same real non-symlink GGUF file, length
  **1,099,627,808 bytes**, identical full-file SHA-256
  `3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4`;
  file identity/size/mtime did not change during either read-only hash.
- A bounded three-request real-generation canary, started together after
  inspecting all slot states, used **two concurrent requests on 19180 and one
  on 19181**, with `max_tokens=3` each and no Task/graph calls. All returned
  **HTTP 200**, one choice each and three output tokens. Durations: **296 ms,
  278 ms, 202 ms**, respectively. All five slot entries were idle again
  afterward. A **second, longer bounded three-way canary** then completed two
  32-output-token requests on `19180` (**1,375 ms** and **1,374 ms**) and one
  32-output-token request on `19181` (**1,200 ms**). All three returned HTTP
  200 and 32 completion tokens each with 27 input tokens, with every slot idle
  again afterward. Neither canary used a Task API or authorized execution.
  These are short, low-context probes—not a long-context throughput benchmark,
  thermal soak, independent process reservation, rollback, or continuous SLA.
  They do **not** justify treating the 4+1 slot entries as five independently
  approved or continuously supportable scheduling slots.
- `assistx-api` is in Docker network `home_scott_git_auto-assist_default`
  (container source IP `172.21.0.6`, bridge gateway `172.21.0.1`). From that
  container, neither LFM3 `:19180` nor `:19181` was directly reachable via
  its own loopback, Docker host gateway, LAN address or Tailscale address.
  The host-only bindings prevented actual fleet model traffic.

## Minimal safe metadata witness

`scripts/lfm3_docker_readonly_witness.py` runs **only from an unprivileged host
process**. It rejects root, public/LAN/Tailnet/loopback bind addresses,
Docker-bridge identity mismatch, different-subnet clients, arbitrary upstream
ports, requests beyond a cap of 8, and any runtime longer than 30 seconds.
It checks the address is on an actual `br-*` or `docker0` interface before
listening. Its only external GET is `/readiness`, from the **exact expected
API-container source IP**. Upstream is pinned to one host-loopback LFM3 port;
it issues only bounded GETs `/health`, `/v1/models`, `/slots` and sanitizes
results to a model alias and slot counts. No arbitrary proxying, no POST,
no model loading, no inference endpoint, no exposed prompt contents, no
credential, no Task URI. Every response states `task_admission=HOLD`.

The **actual temporary physical test** bound only `172.21.0.1:19182` for
four requests (deadline 20 seconds):

| Request | Observed |
|---|---|
| Host source other than `172.21.0.6`: `GET /readiness` | **403**, denied |
| Production API container: `GET /readiness` | **200**, `1/1` idle, no admission |
| Production API container: `POST /v1/chat/completions` | **405**, denied |
| Production API container: `GET /v1/models` | **404**, denied |

The process returned deliberate **exit 2/HOLD**, emitted `CLOSED`, and was
confirmed absent from `ss -ltn` after the test. **No persistent listener,
service unit, firewall rule, shared production source or rollout was created.**

The standalone script includes a 20-second default and a request limit. A
physical rerun must re-resolve the live Docker source/gateway and ensure the
bridge IP still belongs to a private Docker interface: IPs may change when
containers restart. Synthetic tests include negative cases for bad subnet,
LAN/Tailscale listener binding, wrong client, invalid slot state, corrupt
health, oversized response, POST/unknown routes, redirects, and refusal to
infer signed capacity from physical slots. All 29 tests pass locally.

## What must still happen before even one productive Task

The existing canonical generation **896** has expired; the latest observed
fresh approved runtime/model/access/capacity/WorkSupply evidence counters are
all **zero**. A probe or temporary metadata bridge does not constitute a
trusted inference ingress or an approved independent slot. The next real
release slice needs a separately reviewed **authenticated** private model
transport with credential custody, complete independent process/artifact and
access provenance, measured realistic contention and rollback, a single
resource-authorized slot, a fresh owner/signer-approved generation using the
existing protected approval transaction, signed WorkSupplyState, and
resource-fenced CAS/COMMIT before a supervised Task. Do not publish the
host-loopback LFM3 service on LAN/Tailnet or let operator Basic stand in for
machine Task authorization. Until then: **HOLD**.
