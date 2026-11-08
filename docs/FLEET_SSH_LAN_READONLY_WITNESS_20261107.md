# Physical LAN SSH model catalog witness — non-admitting

**November 7, 2026, `x1-370` — read-only research PASS, production Task
admission HOLD.** Direct TCP `192.168.1.86:1234` (`xwing`) returns connection
refused and `192.168.1.79:1234` (Joyner) times out from `x1-370` despite
current interface addresses, host-local model API HTTP 200 and matching
Tailscale model catalogs. UFW is active on both target nodes. The available
non-interactive operator account cannot inspect or change privileged UFW
rules; **no firewall modifications were made**. See issue #569.

## Physically verified transport candidate

An SSH client with **host key checking enforced**, fixed physical LAN IP,
fixed host alias, password/keyboard login disabled, no SSH proxy or agent
forwarding, and one `ssh -W 127.0.0.1:1234` stream successfully issued
`GET /v1/models` to each target's **remote host loopback** API. The result:

| Node | Physical LAN via verified SSH | Exact expected model | Catalog size |
|---|---|---|---:|
| xwing | HTTP 200 | `ornith-1.5-35b-a3b-apex-mtp` | 17 |
| Joyner | HTTP 200 | `joyner-toolcall` | 1 |

The new `scripts/fleet_ssh_lan_model_readonly_witness.py` can reproduce this
as a bounded **opt-in, evidence-only** operation; its default does not create
any SSH process or network request. Run from authorized `x1-370` only:

```bash
python3 scripts/fleet_ssh_lan_model_readonly_witness.py --live --node all
# Exit 2 is always expected: Task admission HOLD regardless of visibility.
```

The script pins the two host aliases, numeric LAN IPs, required model IDs and
remote `127.0.0.1:1234` destination. It reads only the catalog via HTTP GET
using SSH's stdio stream, with a **64 KiB wire cap**, strict complete HTTP
framing, duplicate-header/JSON-field rejection, deadline and child-process
cleanup. No server or local port is opened; no credential is printed; no
arbitrary model prompt is accepted; no systemd unit, Task claim or graph
mutation is made. Failures return only categorical reasons, not SSH stderr
or secret-bearing responses. Output explicitly includes
`direct_lan_http_verified=false`, `production_ingress_authorized=false`,
`approved_parallel_slots=0`, `admission_applied=false` and `task_admission=HOLD`.

The separate GitHub workflow runs **synthetic tests only** with contents-read
permission, pinned actions, no live flag, no provider credentials and no
physical LAN dependence. Use a real authorized x1 SSH identity for the live
witness; synthetic CI cannot validate physical key custody or connectivity.

## Remaining implementation / security gates

This is **not** an approved production inference tunnel. Host-key-verified
SSH catalog access is not equivalent to direct LAN HTTP readiness, an
independently owned credential with rotation, a signed model access path,
resource-owned scheduler fencing or consent to run production Tasks. A durable
SSH-over-LAN bridge would require a dedicated least-privilege service identity,
explicit `ssh` key custody/rotation, strict per-model and per-request policy,
private listener, resource reservation, restart/reconnect fail-closed,
observability, exact physical paths, incident rollback and a signed operator
approval **before** exposure to the production API/worker.

The alternative is an administrator-reviewed, narrowly sourced UFW permit
for the model port, with equally strict production API authentication and
model admission. Do not expose `:1234` to all LAN/Tailnet hosts. Direct-LAN
observer status for xwing/Joyner **remains blocked** until one reviewed
approach is deployed and independently verified.

Canonical signed runtime projection **generation 896 is expired**; the
WorkSupply controller's fresh zero-slot HOLD is correct. Neither this
witness nor any signed *synthetic* LFM3 canary authorizes actual Task
claim/COMMIT. See parent issue #284 and machine Task issue #501.
