# Image-pinned Neo4j ingress evidence — research-only HOLD

**November 3, 2026 — x1-370. DRAFT / HOLD / production NO-GO.**

## Active runtime versus intended production reconciliation overlay

Fleet Commander inspected the RUNNING Docker container named neo4j using
Docker container inspect, local socket listings and read-only source checks.
The immutable Docker image ID was pinned for both Docker snapshots.

| Property | Actual running container | Checked-in reconciliation overlay |
| --- | --- | --- |
| Container identity | neo4j | assistx-production-neo4j |
| Docker Compose project | docker | Separately reconciled production stack |
| Bolt port publishing | All-interface wildcard | 127.0.0.1 only |
| HTTP port publishing | All-interface wildcard | 127.0.0.1 only |
| Network attachments | 3 | 1 control network |
| Effective host firewall | Not independently verified | No deployment proof |

HostConfig.PortBindings has one wildcard publication per graph port,
7474/tcp and 7687/tcp. NetworkSettings.Ports has two entries per port
(IPv4 and IPv6 wildcard), which produces a configured/runtime binding
difference in the new checker.

The current repository docker-compose.yml has a legacy default of
all-interface wildcard for Neo4j Bolt when ASSISTX_NEO4J_BIND is unset,
whereas compose.production.reconciled.yml explicitly specifies loopback
publishing for both graph ports and one control network.

The live Neo4j belongs to Docker Compose project docker, not the name
used by the reconciled overlay. This establishes a DIFFERENCE BETWEEN
RUNNING RUNTIME AND SOURCE-DECLARED SAFER TOPOLOGY. It does not prove
which operator intended the live deployment or whether the overlay was
applied elsewhere.

Unprivileged nftables, iptables, and ufw effective-rule inspection failed
for lack of permissions or runtime availability. OPNsense, Docker NAT,
host firewall and WAN access are NOT independently verified. No external
scan or network access test was attempted. Do not claim confirmed
internet exposure or any successful unauthorized access.

Separately, the production native privilege census has confirmed admin
membership for all three running AssistX API, worker and Hermes adapter
containers: 2 roles, 19 granted privileges, 18 wildcard graph grants,
2 native WRITE grants per service. The risk is the combination of broad
graph credentials with non-loopback port bindings, not a proven exploit.

## New image-pinned read-only safety check

scripts/read_only_neo4j_ingress_gate.py accepts only an immutable
--expected-image SHA256 digest and required --json flag. The only Docker
operation is a fixed, argument-vector Docker container inspect against
the allowlisted runtime container neo4j, twice. No user-controlled
container name, shell/Cypher, TCP connect, network scan or mutation exists.

It checks image, running state, container identity and startup time
before and after; validates port and network map shapes and rejects
restart or network drift. It compares configured and runtime publications,
loopback-only host IPs, exact expected ports, network count, Compose
provenance and the expected reconciled container identity.

It emits only typed risk flags, counts and release-control booleans,
never raw inspect JSON, host IPs, network names, usernames, credentials,
paths, volumes or untrusted error content. Any missing/malformed evidence
fails closed; a green-looking loopback sample still returns HOLD and exit 3,
because independently verified host filtering, signed custody, client
authority and release admission are not proven by a port inspection.

Actual x1-370 image-pinned result:
- expected_loopback_port_publication = false
- off_loopback_publication_count = 2
- network_attachment_count = 3
- published_runtime_binding_mismatch = true
- reconciled_container_name_active = false
- external_reachability_verified = false
- physical_firewall_policy_verified = false
- production_decision = HOLD, change_authorized = false,
  dispatch_authorized = false, exit code 3

## Tests and safe cutover work still needed

The initial focused source suite passed 33 tests (later source graph
regressions and hosted exact-head workflow results must be checked
independently). Cases cover wildcard and non-loopback addresses, IPv4/IPv6
loopback, extra/duplicate/missing published ports, malformed metadata,
wrong image identity, stopped containers, restart/network drift, redacted
readback errors, no runtime mutation, and the checked-in overlay policy.

The safe next action requires a separately owner-approved plan:
1. Verify the real API/worker/Hermes dependency on this specific Neo4j
   database, volumes, backups, host-addressed clients and recovery keys.
2. Have an authorized administrator inspect effective nftables/iptables,
   Docker NAT, OPNsense forwards and Tailnet paths without modifying
   policies or publicly probing ports.
3. Validate a disposable/canary deployment with loopback-only host
   publishing, a private Docker control network, authenticated client
   access and rollback of every legitimate dependent workload.
4. Stage separate non-admin read-only observer credentials and a
   controlled Task mutation gateway, with signed admission, claim CAS
   and independently recorded receipts.
5. Preserve zero approved work slots until all exact-head ingress,
   writer, independent custody, and production approval gates pass.

**NO live container, firewall, NAT, database role, password, Task,
graph, service, model, route, storage volume or approval was modified.**
