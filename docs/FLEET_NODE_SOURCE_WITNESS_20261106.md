# Fleet source witness — read-only five-node HOLD, November 6, 2026

**Classification: SOURCE RESEARCH ONLY. No running process, agent identity, execution
slot, credential, lease, claim or resource commitment is approved by this witness.**

## Evidence and exact candidate

- Upstream safety contract: draft Auto-Assist #550 → #551 → #555, with all five
  legacy Task routes denied for machine identities; positive identity-only
  diagnostics never grant Task admission.
- Exact #555 source commit: `63ce16f02d1a11be347f1cd83162e7dcf0183494`.
- Candidate `src/assistx/fleet_node_agent.py` SHA-256:
  `02d7ea9f9ca637376a810ad0f537e8cf2b0e200090a7e506bbb215eafe5057b0`.
- Existing five-node shared source SHA-256:
  `2aef0a3c9cfa51faf6b7d1cfa9bead27720789ce0b25bf315c494022f8cc0f3d`.
- Nodes reached: `xwing`, `deathstar`, `destroyer`, `lenovo`, `optiplex`.
  All five user-level `fleet-node-agent.service` units were active, all five
  failed source-hash pinning, and all five returned a categorical HOLD/exit 2.
- Deathstar and Lenovo also reported on-disk source and wrapper modified after
  their active process started, making in-memory code identity more uncertain.

## Reproduce without altering fleet nodes

Run on `x1-370` from this worktree, for each already-authorized SSH alias:

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes xwing \
  python3 - \
  --expected-source-root /media/scott/SSD_4TB/hermes-home/home_scott_git_auto-assist/src/assistx \
  --expected-source-sha256 02d7ea9f9ca637376a810ad0f537e8cf2b0e200090a7e506bbb215eafe5057b0 \
  < scripts/fleet_node_source_witness.py
# Exit code 2 is deliberate HOLD, even when a future on-disk hash matches.
```

The witness parses the systemd user-unit path, service PID start time and the
literal `ROOT` in its Python wrapper. It neither imports the wrapper nor starts
an agent, calls an API, reads credential values/process argv, copies files to
nodes, or writes services. The JSON output includes categorical reasons and
file digests only. A disk SHA is **not** an attestation of previously loaded
Python bytecode; service restart, immutable image provenance and approved
node-by-node credential rollout are separate procedures with separate gates.

## Release HOLD

Keep legacy machine Task polling/claim/mutation routes at 403. Do not copy
operator Basic credentials, swap active shared source trees, restart five
services en masse, update production tokens, or enable background workers.
Next admissible step: reviewed pinned source/image and rollback for **one**
approved node, independently approved identity-only endpoint/origin and
credential owner approval, then supervised probe showing `task_admission=HOLD`.
Productive machine execution requires a separate signed, owner/resource-bound
policy with approved positive slots, claim fencing and externally witnessed
COMMIT; none of this is established here.
