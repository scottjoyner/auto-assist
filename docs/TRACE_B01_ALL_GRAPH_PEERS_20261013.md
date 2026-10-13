# B01 — all running Neo4j network peers (2026-10-13)

**Research only / NO-GO production.** Stacked on draft B02 #342's
existing isolated task broker fixture. This adds a strictly read-only
Docker network census and extends the *disposable* network-denial probe
to match the current live deployment's seven graph-network peers.

## Live, read-only evidence

Two separate read-only probes agreed that the currently running AssistX
API and worker still connect directly to Neo4j Bolt. The original #294
gate returned `NETWORK_BYPASS_PRESENT_NO_GO` with **exit code 2**.
The new opt-in census inspects only Docker container names, network
memberships, and Neo4j host-published port bindings, **not** environment
variable values, database credentials, graph data or logs.

At observation time: **11 running containers**, **3 Neo4j networks**,
**7 non-Neo4j containers** sharing one or more graph networks.
| Live container | Shared Neo4j Docker network(s) |
| --- | --- |
| assistx-api | git_default, home_scott_git_auto-assist_default |
| assistx-worker | home_scott_git_auto-assist_default |
| assistx-hermes-adapter | home_scott_git_auto-assist_default |
| assistx-redis | home_scott_git_auto-assist_default |
| auto-assign | git_default, home_scott_git_auto-assist_default |
| git-redis-1 | git_default |
| git-router-1 | git_default |

Neo4j host-publishes TCP 7687 via host **7688** and HTTP 7474 via host
**7475**, both with wildcard Docker HostIp. Docker membership proves a
potential network path, not credential possession or execution authority.
No connection probes were attempted in the five additional live peers;
their runtimes may not contain a Python/TCP test client. No claim of
Internet reachability or Tailnet Serve authorization is made.

Manual census: `ASSISTX_B01_CENSUS_READONLY=1 python3
tests/trace_b01_graph_peer_census.py`. Exit 2 is the expected NO-GO
while any graph peer or host-published graph port persists.
## Disposable network acceptance

The existing Neo4j 5.26 Enterprise graph/broker/worker fixture was
extended with six additional lightweight **Python role stand-ins**:
API, Hermes adapter, both Redis containers, auto-assign, and router.
The worker remains the seventh role. All seven use the isolated actor
network; only the broker bridges to the internal graph network. No
graph host ports, external network or persistent graph mounts are used.

Physical acceptance: **PASS, exit 0**, 57.44s command runtime.
Six added peer stand-ins each denied graph Bolt/HTTP by literal graph
IP and graph hostname, and successfully opened the broker TCP socket.
The worker additionally proved broker-based Task read, role-scoped
Neo4j read, graph write denial, missing bearer denial and arbitrary
query/parameter rejection. All fixture containers/networks are removed
by the existing `finally` cleanup, including on test failure.

**Limitations:** The Redis, router, Hermes and API peers are Python
network stand-ins; no real image, workload, health check or restart
behavior was tested. Browser/Tailnet and host-published-port route
testing remains separate. Broker fixed reads do not prove that all
source consumers or privileged effects are migrated.
## Production migration gate (all remain blocked)

1. Map each live graph-network peer to its actual role, direct Neo4j
   client use, health dependencies, and service discovery paths.
2. Migrate API/worker graph operations under explicitly reviewed
   broker plans and receipt/custody authority. PR #301's static census
   is not a complete dynamic runtime call graph.
3. Authenticate the broker, independently verify graph writer custody,
   and prove direct Neo4j credentials are absent/revoked from all
   non-broker roles; do not infer from an open TCP port alone.
4. Validate real images and APIs on a **separate disposable topology**,
   with isolated peer networks, broker reachability, and denial of
   Bolt/HTTP via DNS, raw IP, host published ports, and Tailnet paths.
5. Verify independently trusted ingress identity and reject forwarded
   headers not attested to the known proxy peer.
6. Independently approve any live Docker/Compose/Tailscale/credential
   change only with staged rollback, then repeat the live negative
   probes; no bypass connections may remain.

**No production topology, graph, credentials, admission, release or
failover were changed. B01/B02/B06 stay HOLD.**