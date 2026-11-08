# LFM3 internal-network egress fence — reproducible non-admitting proof

**2026-11-08, x1-370. Source-only research. Production Task admission HOLD.**

## Specific problem closed in staging

The merged two-container model/gateway namespace proof (#579) denied direct
host/API TCP access to the model service on its Docker interface, but ran on
the same `home_scott_git_auto-assist_default` Docker bridge as existing
services. The model and gateway still inherited ordinary bridge outbound
connectivity. They could potentially reach outside resources under an unsafe
future code path, and the cached image was referenced by a mutable tag.

This work adds an **independent disposable `docker network create --internal`
bridge** that cannot route IPv4 traffic to public destinations under the
tested configuration. It also pins the preexisting local Docker image by
**full image-ID SHA-256** and checks the actual GGUF bytes against the known
reviewed SHA-256 before any container is launched. No image is pulled, copied
or built by the physical probe.

## One-shot components and custody

`scripts/fleet_lfm3_internal_egress_stage.py` defaults to a no-effects JSON
HOLD and exit code 2. An explicit `--live` probe:

- Refuses root, missing/wrong locally cached image ID, GGUF bytes not equal
  to the reviewed digest, or any previously existing staging container or
  network name. It checks the model using a bounded streaming SHA-256, not
  large in-memory model bytes. Docker input validation occurs before network
  creation. The stage is pinned to fixed, non-production container/network
  names.
- Creates one new **nonattachable, non-ingress, internal-only** Docker bridge.
  Independently inspects `Name`, `Driver`, `Internal`, `Ingress`, `Attachable`
  and the exact live attached-container census at every step. Other running
  production containers are *never* attached or reconfigured.
- Launches a separate nonroot CPU LFM3-1.7B model process with read-only GGUF
  and runtime mounts, one inference slot, 3-GiB/3-CPU ceiling, no privileges,
  no published ports, and model listening **only on its own**
  `127.0.0.1:19185`. Confirms health from inside the container, and host
  denial of the model bridge IP on port 19185.
- Launches one separate read-only/nonprivileged **test client** on that same
  internal-only bridge, then proves the client cannot connect directly to
  the model at its bridge IP/port. A separate nonroot HMAC gateway container
  shares the model's *exact network namespace* via `--network container:...`.
  The gateway only exposes port 19205 **on this internal test bridge** and
  requires one source IP and HMAC-bound method/path/date/nonce/body for the
  existing fixed benign `Reply OK.`/4-token test. No Task code runs.
- Uses the previously reviewed host-side fenced SQLite slot ledger with a
  **disposable synthetic-only** grant. The gateway acquires one physical
  model slot, validates the generation/fence before model inference, uses an
  eight-second killable child within an eleven-second lease, and must
  revalidate **and release the exact slot** before returning an HTTP 200.
  Three requests (wrong HMAC, valid fixed canary, nonce replay) exhaust the
  listener's exact request budget; the gateway exits 2/HOLD.
- Explicitly verifies the model, gateway and client cannot open TCP sockets
  to `1.1.1.1:443` or `8.8.8.8:53`. If any external connection succeeds,
  or a Docker-network metadata check fails, the entire proof fails closed.
  These checks are IPv4 TCP egress *witnesses*, not a formal firewall audit,
  and do not prove all conceivable side channels are blocked.
- On any Python exception, cleans up **only** the three exact staging
  containers and the named ephemeral network, followed by the private
  synthetic ledger directory. SIGTERM requests a failure and runs the same
  cleanup path. No stage image, network, listener, model process or database
  remains after a successful proof. No production API, Task, credential,
  firewall, work-supply, Graph or systemd operation is touched.

## Observed physical results, November 8

| Evidence | Independent physical observation |
|---|---|
| New Docker bridge | `Internal=true`, `Driver=bridge`, `Ingress=false`, `Attachable=false` |
| Mutable image-tag substitution | Rejected by pinned local image ID |
| GGUF file tampering | Rejected by streaming SHA-256 check |
| Normal host TCP access to new model | DENIED |
| Separate test-client direct model TCP | DENIED |
| External IPv4 TCP from model | DENIED |
| External IPv4 TCP from test client | DENIED |
| External IPv4 TCP from gateway | DENIED |
| Correct signed fixed LFM3 request through gateway | **HTTP 200; 4 completion tokens** |
| Wrong HMAC and repeated signed nonce | Both HTTP 403 DENIED |
| Resource slot | Validated and released before positive result |
| Gateway shutdown | Exit 2/HOLD after three requests |
| Residual model/gateway/client containers | Zero |
| Residual private Docker staging network | Zero |
| Residual synthetic SQLite ledger directory | Zero |
| Existing production API and Neo4j | Healthy, unchanged |

The physical probe does not attach the production `assistx-api` container to
the new internal-only network; it uses a **disposable separate test-client**
container on that network. The previous #579 proof already verified a real
production API-container-to-gateway staging HTTP request on a different
non-internal bridge. This new proof adds **egress isolation**, rather than
claiming a production API cutover.

## Remaining production and security boundaries

The test uses synthetic generation `897` and a zero-filled marker, **not a
real owner-signed grant**. Even with network and image identity checks,
Docker administrators can inspect/reconfigure containers or networks, the
source is mounted from an unsealed working tree, and original host LFM3
processes remain accessible outside this staging namespace. The localhost
model process may continue computation after its HTTP client is terminated;
there is no production-grade resource-owned cancellation/COMMIT handshake.
A network with `Internal=true` can permit internal communication among its
members, which is desirable for the test, but is not a complete physical
security boundary against privileged host actors or every IPC/side channel.
A 32-byte one-shot HMAC token is ephemeral test authentication, not a service
identity with rotation, issuance, audit and scoped Task credentials.

**Task admission remains HOLD.** Production canonical generation 896 is
expired, WorkSupply generation 0 has zero approved slots, machine Task GET
is HTTP 403, owner public trust `/etc/assistx/trust/runtime-approval-owner.pub`
is not installed, and the old auto-renew timer remains disabled. No claim,
Graph write, owner grant or service cutover occurred.

The next authorized production work requires an independent owner-admin
signature/verification root, a reviewed current roster and physical runtime
capacity approval, an immutable image that embeds signed verified code and
provides dedicated gateway/model identity, an actual sealed grant ledger,
controlled network and resource-side Task claim/COMMIT fencing, nonbypassable
model process ownership, safe rollback and a supervised one-node canary.
Do not self-sign, reuse the October approval, open WAN ingress, attach the
production API to a new network or relax machine Task 403 simply to run this.

Related release tracking: auto-assist #284, #501 and #569. Prior co-namespace
source: #579. This staging source and tests are non-admitting and read-only to
all existing production infrastructure.
