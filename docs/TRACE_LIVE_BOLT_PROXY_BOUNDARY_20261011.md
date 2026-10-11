# AssistX #148 — Live Neo4j reachability & mobile identity provenance, adversarial boundary gate

**2026-10-11 · Research-only / draft; production graph admission and automatic failover NO-GO.**

## Executive outcome

The earlier three-voter Raft quorum, independent Neo4j closure witness, RBAC-scoped policy writer and authenticated idempotency experiments are **not sufficient for production** because at least one required isolation assumption fails on the real x1-370 fleet deployment.

**Live negative (network reachability):** `docker exec assistx-worker` and `docker exec assistx-api` performed **TCP connect only** to `neo4j:7687` (Bolt) and `neo4j:7474` (HTTP). All four connects succeeded. No credentials, Neo4j authentication, Cypher or graph writes were attempted. The result proves a bypass-capable **network path**, not an authenticated database-command privilege. It is a hard negative for the claim *“all graph traffic from workers can only pass through the quorum-gated entrypoint.”* As long as these paths exist, Raft grants at a research gateway cannot provide nonbypassable worker-to-Neo4j admission.

### Physical topology found

| Element | Observed live (read-only inspect) | Implication |
|---|---|---|
| `neo4j` image | `neo4j:5-enterprise` | Live runtime differs from the disposable research fixture |
| Neo4j Docker networks | `docker_default`, `git_default`, `home_scott_git_auto-assist_default` | Multiple shared network attachment paths |
| Neo4j Bolt host mapping | container 7687 → host wildcard port 7688 | Host-exposed entry path; remote firewall/Tailnet policy not proven |
| Neo4j HTTP host mapping | container 7474 → host wildcard port 7475 | Additional host-exposed database management/API surface |
| Tailscale Serve configuration | TCP entry `7687` listed | Potential additional Tailnet entry; backend and access controls not independently validated |
| `assistx-worker` networks | `home_scott_git_auto-assist_default` | Shares a network with Neo4j; Bolt TCP handshake succeeded |
| `assistx-api` networks | `git_default`, `home_scott_git_auto-assist_default` | Shares networks with Neo4j; Bolt TCP handshake succeeded |
| Graph compose origin | `/home/scott/docker/neo4j-ssd.yml` | Defines unscoped `7475:7474` and `7688:7687` host mappings |
| AssistX API | `127.0.0.1:8000` host mapping | Loopback host publication is more restricted, but local/bridge paths still matter |

Do **not** equate an open TCP connection with successful Cypher or authorization. Database roles, credentials, privileges, outbound host firewalls and actual Internet reachability were not tested. TCP listener and host mapping observations alone do not establish whether an untrusted Internet client can successfully access Neo4j.

## Mobile trusted-proxy identity negative

`src/assistx/mobile_agent_routes.py` accepts `Tailscale-User-Login` as authenticated identity when the environment names that header and the login passes `KIPNERTER_TAILNET_ALLOWED_LOGINS`. The helper does not internally validate that the header was minted by Tailscale Serve rather than passed to the API over an alternate allowed local/bridge route.

An offline test against the **actual helper**, using only synthetic `synthetic-boundary@example.invalid` and a mocked allowlist, shows the helper accepting an identity on an ASGI request whose client address is TEST-NET `198.51.100.42`. This is a *code-level counterexample to enforcing proxy provenance inside the helper*; it is **not evidence that a live real account has been spoofed**.

Read-only live x1-370 GET `/api/v1/auth/whoami` without identity returned 401; a deliberately invented non-allowlisted login header returned 403. This validates the configured deny behavior for those inputs, **not** protection from an attacker who can inject an already allowlisted login. No real account impersonation, production credential probe or destructive testing was performed.

The endpoint is fronted by Tailscale Serve 8443 from the tailnet and is published on the host's loopback, but **loopback is not a cryptographic proxy attestation**. Same-host processes, bridge-connected containers, alternate proxies and host-network workloads must be included in the trust-boundary review. Source-level acceptance of a header without peer provenance must not be silently promoted as live SSO security.

## Executable guard and reproducibility

- `tests/probe_trace_bolt_topology_readonly.py`: strictly opted-in inspection `ASSISTX_BOUNDARY_READONLY=1`; optional `ASSISTX_BOUNDARY_TCP_PROBE=1` sends only one TCP connect per worker/API container. No Neo4j authentication, credentials, writes, Docker network changes or service restart. Returns exit **2** deliberately when network bypass is found; exit 0 only when isolation remains *unproven* (not a promotion approval).
- `tests/test_trace_bolt_topology_readonly.py`: **11 offline tests PASS**, covering network overlap, wildcard and IPv6 host publication, TCP-only evidence limitations, incomplete inventory denial and the synthetic allowlisted-header provenance counterexample.
- `.github/workflows/trace-graph-entry-research.yml`: adds the tests to dedicated CI and compiles the manual-only probe; the CI runner never contacts fleet Docker.

On live x1-370 the command:

`ASSISTX_BOUNDARY_READONLY=1 ASSISTX_BOUNDARY_TCP_PROBE=1 python3 tests/probe_trace_bolt_topology_readonly.py`

returned exit **2** and JSON `verdict=NETWORK_BYPASS_PRESENT_NO_GO` with `assistx-worker` and `assistx-api` both direct Bolt TCP connected. The exit 2 is the **desired failing safety gate**, not a test malfunction.

[Structured observation](../research/evidence/assistx-live-bolt-proxy-boundary-20261011.json).

## Dependency-safe staged remediation (NO live changes in this PR)

| Gate | Owner boundary | Smallest acceptance | Release posture |
|---|---|---|---|
| G0 / immediate | Fleet inventory | Inventory *all* real Neo4j callers, their Cypher plans, health/status dependencies, and legitimate read/write side effects before touching networking | Block automatic production graph failover |
| G1 | Identity ingress | Introduce authenticated provenance at ingress not relying solely on a forwarded login header; prove via local, bridge, Tailnet and alternate proxy paths with **synthetic test identities** | Do not assume 8443 termination is universal |
| G2 | Graph broker | Implement isolated graph-entry broker with least-privilege role and non-exportable per-service credential, allowlisted operations, immutable audit-before-Bolt, quorum CAS before physical execution; migrate necessary worker/API calls | Feature parity verified in disposable full-stack |
| G3 | Container networks | Connect Neo4j only to a broker-only isolated internal network; worker, API, and general clients cannot reach Bolt/HTTP directly, including via wildcard host ports and Tailnet Serve TCP alternate routes | Network deny must PASS from all fleet nodes and namespaces |
| G4 | Neo4j RBAC | Demonstrate actual Neo4j roles: worker/anonymous cannot authenticate or execute direct Cypher; broker principal has only reviewed privileges; revoke/bypass negative tested | Server-side effect fencing still independent open gate |
| G5 | Full failure integration | Rerun three physical voters, real Bolt transaction, independent witness, leader loss, term turnover, quota/restore and forged header/fresh-client-retry negatives | Remain draft until acceptance + rollback |
| G6 | Operator rollout | 1/3/5/10 authenticated clients, history/retention, p95/p99, normal work preserved, staged rollback demonstrated | Separately authorized only |

**Do not sever the live Neo4j networks or change wildcard published mappings immediately:** `src/assistx/api.py`, `neo4j_client.py`, `router_integration.py` and other modules currently call Neo4j directly. Removing paths without migration would interrupt active API/worker functionality and possibly corrupt operational assumptions. Build a disposable broker and test feature parity first; then stage the deployment with an explicit rollback and live change authorization.

## Explicit residual unknowns

Credentials/roles have **not** been checked. Direct-connection success is not Cypher execution. Tailscale Serve TCP 7687's backend/ACL was not verified. Real allowlisted-identity injection was **not** attempted. Physical connection checks from other fleet nodes were not completed conclusively. The research branch does not mount the new auth route or alter any running service.

**Disposition:** Independently tested network reachability contradicts nonbypassable worker graph-entry isolation. Keep issue #148 OPEN, all research changes draft-only, and production admission/automatic failover **NO-GO**.
