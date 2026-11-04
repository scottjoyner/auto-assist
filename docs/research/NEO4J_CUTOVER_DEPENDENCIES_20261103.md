# Active Neo4j client and secondary forwarder dependency gate

**x1-370, November 3, 2026. DRAFT research-only HOLD / production NO-GO.**

## Verified read-only runtime topology

Earlier #530 and #534 established that the running Docker container
named neo4j publishes two wildcard host ports and has three network
attachments, while a separate reconciled production overlay expects
loopback-only host publishing and a single control network. The active
container belongs to Compose project docker and its actual source is
neo4j-ssd.yml, not the reconciled overlay. Both active published host
port entries omit an explicit host IP.

| Client or dependency | Running configuration |
| --- | --- |
| AssistX API | Docker host gateway port 7688 (Bolt) |
| AssistX worker | Docker host gateway port 7688 (Bolt) |
| Hermes adapter | Docker host gateway port 7688 (Bolt) |
| PhoneLog | Loopback port 7688, with host networking |
| Neo4j Docker Bolt | host 7688 maps to container 7687, wildcard |
| Neo4j HTTP | host 7475 maps to container 7474, wildcard |
| Neo4j persistent mounts | /data, /import, /logs: writable bind mounts |
| Secondary systemd listener | assistx-neo4j-bolt-forward.service, active |
| Forwarder executable | socat, non-loopback TCP listener on 7687 |
| Forwarder destination | loopback port 7687 |
| Matching host loopback listener on 7687 | Not observed with ss |

The previously unidentified host listener on port 7687 is managed
by an active socat systemd unit. It is separate from Docker's
published Bolt host port 7688. Its destination is loopback:7687;
the available host socket snapshot showed only a non-loopback
listener on that port. The forwarder destination is consequently
not verified. It may be misconfigured, but no TCP connection or
behavioral probe was attempted and failure has NOT been proven.

PhoneLog uses Docker host networking. Its loopback:7688 address
therefore refers to the host, and its configuration points to the
same published host endpoint as API, worker and Hermes. Successful
authenticated PhoneLog graph operations were not tested.

The three graph mounts were observed as writable Docker bind mounts
with existing source directories. This proves neither backup quality
nor restoration. No independently verified backup, snapshot
consistency, restore exercise, rollback plan, host firewall/NAT,
OPNsense/Tailnet policy or signed custody has been established.

The bounded running-container inventory found four graph client
configurations. It cannot discover all native host processes,
archived clients, external connections or out-of-band administrators.

## New source-only cutover dependency preflight

The script scripts/read_only_neo4j_cutover_dependencies.py:

- Requires an immutable Docker SHA256 image pin and a JSON output flag.
  Runs the existing double-inspection image-pinned Docker ingress
  checker both before and after the dependency inventory.
- Invokes only fixed docker ps, docker inspect, ss, systemctl is-active
  and systemctl cat read-only command arrays with bounded outputs.
- Parses all running containers' Neo4j endpoint settings but prints
  no raw URIs, usernames, passwords or other environment values.
  Returns aggregate counts and fixed endpoint categories only.
- Checks the secondary forwarder's listen/destination scopes and
  whether a loopback destination socket was observed. It does NOT
  verify an actual end-to-end forwarding handshake.
- Checks expected bind mount targets, but does not inspect data
  contents or verify a restore.
- Does not query graph content, create a database session, change
  Docker, restart systemd, connect to a TCP port, edit firewall
  rules, grant privileges, trigger inference or dispatch Tasks.
- Always reports production_decision=HOLD,
  cutover_authorized=false, dispatch_authorized=false and exit 3.

The actual x1-370 read-only run verified all four known client
configurations, three bind mount targets and the active secondary
forwarder. It reported no matching loopback port 7687 listener.
Backup/restore, host filtering and database role separation remain
unverified. No raw hostname, host IP, network name or secret was
emitted in the result.

## Tests and safe cutover dependencies

Focused tests cover invalid client URIs, host-network PhoneLog,
extra/missing/duplicate clients, malformed forwarder configuration,
loopback versus wildcard listeners, absent mount targets, untrusted
ingress receipts, bounded commands and redacted failures.
Their success proves only the deterministic source contract.
Broad Task writer/physical graph regressions and hosted exact-head CI
must be tracked independently.

Before changing the active neo4j-ssd.yml or forwarder:
1. Independently verify forwarder endpoint health, owners, authorized
   users, Tailnet callers and older media processing clients. Do not
   delete/restart the service based on a socket snapshot.
2. Obtain authorized read-only Docker NAT, host nftables/iptables,
   OPNsense and Tailnet policy evidence.
3. Inventory actual Neo4j data/import/log bind mounts and backups,
   and complete an approved disposable restore and rollback test.
4. Canary a loopback-only/private-network publication with API,
   worker, Hermes, host-network PhoneLog, secondary forwarder,
   backups and other client dependencies.
5. Migrate the three broad admin/WRITE database principals via
   separately approved scoped observer and authenticated Task
   mutation gateway credentials, preserving claim CAS and
   independent signed receipt custody.
6. Keep static writer census HOLD (49 unresolved sites), signed
   fleet admission expired and production work slots at zero.

**No live service, Docker network, port binding, database role,
credential, Task, model, graph record, firewall rule, signed
projection or dispatch was changed.**
