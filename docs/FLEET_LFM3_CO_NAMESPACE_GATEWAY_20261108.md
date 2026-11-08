# LFM3 model-inference isolation: co-namespaced slot-fenced HMAC gateway

**2026-11-08 / x1-370. Non-admitting, operator-only research proof.**

## Prior blocker and tested improvement

#578 demonstrated an HMAC-authenticated private Docker request through the
host-local SQLite slot lease to a genuinely resident 4-token LFM3 model. The
inference listener nevertheless remained independently reachable at the host
`127.0.0.1:19181`, permitting host processes to bypass the host gateway. A
second separate model-container experiment demonstrated that a CPU inference
server can work inside a container network namespace, listening on internal
loopback without exposing its port to the host/API, but it was not connected to
the authenticated gateway.

This source now **joins those two previously independent physical proofs**:

- Disposable LFM3 **model container**, nonroot UID1000, 3-GiB/3-CPU bound,
  `--read-only`, only read-only model/runtime mounts, no privileges, no host
  network, no published ports, and an existing Docker bridge shared by the
  currently running `assistx-api` container. The model *only* binds its own
  namespace `127.0.0.1:19185`, alias `lfm3-isolated-namespace-poc`, fixed
  one-slot CPU configuration. It is not the existing host LFM3 instance.
- Separate disposable **gateway container**, nonroot UID1000, 1-GiB/2-CPU
  bound, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, no published
  ports; it uses `--network container:<model-container>` to **share the model
  container's network namespace**. Its HMAC gateway binds the model
  container's private bridge IP at port 19205. Its code is a read-only
  worktree mount. Only its own one-time synthetic SQLite ledger mount is
  writable. No Docker socket, Apple tokens, Neo4j secrets, API credentials,
  SSH agent or service signing keys are mounted.
- Real `assistx-api` container, on the same existing private Docker bridge,
  connects only to the gateway's exposed port. Its actual direct TCP connect
  attempt to `model-container-IP:19185` is **refused**. The host's direct
  network attempt to the same model port is also **refused**. The gateway can
  reach its *own* namespace-local `127.0.0.1:19185` to perform inference.
  The model is neither attached to a host listener nor port-published.
- The existing #578 fixed `POST /staging-canary` HMAC implementation is reused
  (not reimplemented). It binds signed method/route/date/nonce/body, fixed
  prompt, allowed API source IP, ≤4 tokens, nonce replay denial, one-shot
  maximum requests and short uptime, the #577 slot CAS, a killable 8-second
  HTTP child, post-effect lease check **and confirmed resource release before
  HTTP success**. A distinct `fleet_lfm3_namespaced_slot_gateway.py` selects
  only the *namespace-local* model as the upstream inside the spawned
  process. It cannot forward arbitrary user-selected URLs/model routes.
- Real source/custody proof: the operator verifies the Docker network name,
  source API IP, model container's exact network membership, no model port
  bindings, gateway `HostConfig.NetworkMode=container:<same model>`, model
  loopback health, direct host/production-API bypass-denial, result budget,
  bad HMAC denial, wrong-source HMAC denial, nonce replay, and complete
  listener/container/fixture removal. HMAC key is delivered over stdin to
  each one-shot process, never stored in source, container env, argv or disk.

## Observed physical witness

On November 8, 2026, the operator explicitly invoked
`scripts/fleet_lfm3_namespaced_operator_probe.py --live` from `x1-370`.
The script uses the existing, physically inspected `assistx-api` network
`home_scott_git_auto-assist_default`, a cached local image, and the original
GGUF; it does not install packages or pull an image. It created only two
short-lived containers, removed both, and deleted its temporary 0700/0600
synthetic private ledger after the test. The observed results were:

| Check | Actual result |
|---|---|
| Model health from inside model's network namespace | PASS |
| Host direct TCP to model's private interface model port | DENIED |
| Existing API container direct TCP to model's private interface model port | DENIED |
| Model and gateway network namespace identities | SAME, verified |
| Signed host request from disallowed address | 403 DENIED |
| API container wrong HMAC | 403 DENIED |
| API container correct HMAC + fixed benign LFM3 | **HTTP 200 / 4 completion tokens** |
| Exact same signed nonce replay from API | 403 DENIED |
| Slot acquired, checked before/after bounded inference, released before success | PASS |
| Gateway exited code 2/HOLD and listener stopped | PASS |
| Both containers, one-time SQLite grant and listener removed | PASS |
| Existing host LFM3 and AssistX API | HEALTHY, unchanged |
| Task admission or owner authorization | **HOLD, zero approved slots** |

The model's internal alias is fixed in the gateway code and differs from the
client's pre-existing signed canary **envelope alias**; client arguments are
not forwarded upstream. The observed response contains only a bounded token
count and HOLD metadata, never generated text, a lease token or HMAC secret.

## Security boundaries: what this does and does not solve

**Proven:** One live two-container inference path cannot be reached around
its signed slot gateway using an ordinary host/API TCP connection to the model
container's bridge address; the model has only namespace loopback ingress.
The gateway's source-IP and HMAC checks are additional controls. Tests also
cover wrong model ID, busy or ambiguous model slots, malformed usage,
replayed HMAC, timeout, released/revoked grant and invalid namespace binds.

**Not proven:** Host administrators with Docker access can `docker exec`,
enter the namespace or change its configuration. Other local model processes
on the host remain independently reachable and were intentionally not
stopped. The new LFM3 namespace is still attached to the existing Docker
bridge, whose other containers may have outbound access; there is **no
production-level firewall/egress policy**, dedicated model identity, or
persistently sealed image. Staging uses a mutable path to reviewed source
and a **synthetic zeroed owner-receipt marker**. It has no owner Ed25519
signature, real canonical generation 897, task authority, Task claim or
commit, production slot provisioning, or durable exclusive model custody.
Model-client process termination does not guarantee that the upstream model
aborts tokens still being processed; this remains a resource-side generation
and completion-fence review item.

**No production rollout:** The two-container proof uses a disposable CPU
model, not a replacement for the currently pinned production checkout.
All services, port bindings, firewall rules, credentials, Graph projection,
model process assignments and Task permissions remain unchanged. The source
scripts default to exit 2/HOLD with no Docker invocation; `--live` must be
explicit and both processes return non-authorizing evidence. New GitHub CI
executes only synthetic tests, never `--live` or `--staging`, and cannot
provision a grant or connect to production inference.

## Required before any Task-enable gate may advance

1. Independent, root-controlled public verification key and independently
   issued **real** Ed25519 owner approval tied to the current exact model,
   runtime/paths/required-node roster and approved capacity. Canonical
   generation **896** was expired; `WorkSupplyState` showed generation **0**
   and zero approved slots. An all-zero synthetic digest is not authority.
2. Convert this research container topology into a **reviewed, immutable,
   credential-custodied production release**. Give a dedicated limited model
   user exclusive ownership of the upstream process/socket; prevent other
   host model/access-path bypasses without breaking other workloads. Apply
   network egress and bypass restrictions, signed image identity, startup
   health checks, model memory budget, Task identity and rollback controls.
3. Connect the host resource CAS to a real owner-provisioned slot grant,
   verified request identity and a resource-owned Task claim/COMMIT bound
   to the signed generation. Exercise restart, crash, expiry, concurrent
   execution, stale epoch, replay, source spoof and crossed-node negatives,
   and ensure inference is aborted/cannot commit on revoked authority.
4. Only after a controlled approved staging/release change, run one fully
   supervised positive Task canary. Do not reactivate old October keepalive,
   self-sign approval, broaden UFW or bypass the 403 machine Task denial.

The main release issues remain [#284], [#501] and [#569].
