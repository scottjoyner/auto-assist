# xwing single-node source stage — November 7, 2026

**RESEARCH PASS; live fleet Task admission HOLD.** No production source, service,
credential, endpoint, Task record, resource authorization or signed projection
was modified. This deliberately does **not** fix the five live 401 workers yet.

## What was actually staged

- Host: `xwing` only; current service `fleet-node-agent.service` remains active
  with its original legacy source. The other four agents were not touched.
- Candidate commit: Auto-Assist draft #555 exact SHA
  `63ce16f02d1a11be347f1cd83162e7dcf0183494`.
- Private, separate stage:
  `/home/scott/.local/share/assistx-staging/63ce16f02d1a11be347f1cd83162e7dcf0183494`
- Three files extracted from `git archive` at that commit, not from a dirty
  worktree. A blank `src/assistx/__init__.py` was added for an inert minimal
  test harness. **This is not a production wheel/image or installed service.**
- Stage checksum file `SOURCE_SHA256SUMS` was checked on `xwing`:
  `fleet_node_agent.py` `02d7ea9f9ca637376a810ad0f537e8cf2b0e200090a7e506bbb215eafe5057b0`;
  `lm_studio_client_auth.py` `12a1a444948b0c115a455f2664d88b2feea09977ea75b20af1d795a213c29467`;
  `recovery_runbooks.py` `c946869ab6ba3c9907879e7bbb15a5f5d77abe2a331ed1b915c640e8e4623859`.
- Directory was created with owner-only permissions (0700); source files 0600.
  The old active shared source remained SHA-256
  `2aef0a3c9cfa51faf6b7d1cfa9bead27720789ce0b25bf315c494022f8cc0f3d`.

## Executed one-shot boundaries

1. Sanitized environment with **no node token or approved origin**:
   `python3 -m assistx.fleet_node_agent --identity-probe-only` emitted
   `MISSING_OR_MISMATCHED_NODE_IDENTITY`, `task_admission=HOLD` and exited 2.
2. Synthetic *loopback only* HTTP server at `127.0.0.1` with a fake node ID
   and fake token returned a four-field, identity-only `task_admission=HOLD`
   response. Exactly **one GET `/api/fleet-node/identity`** was received; no
   operator Basic header, Task URL or model request. Staged agent emitted
   `identity_verified=true`, `production_admission_permitted=false`,
   `task_admission=HOLD` and still exited 2.
3. Local synthetic test cases are recorded in
   `tests/test_fleet_node_identity_staging.py` and run in GitHub's
   read-only `synthetic-hold-witness` PR workflow.

## Next approval and rollback boundaries

A positive synthetic loopback probe is **not** approval of a real node principal,
trusted HTTPS origin, production image, admission, graph fence or signed
resource/COMMIT authority. Do not put credentials into CLI arguments, reuse
operator Basic credentials, swap the active shared root or restart the five
agents. A named credential owner must independently approve a **single** node
principal and origin; the supervised real identity-only probe must exit 2,
show `task_admission=HOLD`, and prove all five legacy routes still deny
machine credentials. A separate release decision is required before any
source cutover or machine Task execution.

There is no installed stage service or new running process to roll back.
If the isolated staging files are later retired, remove only the exact
private `assistx-staging/<commit>` directory after verifying its pinned
manifest and that no process uses it. **Never delete the active legacy
`/media/scott/SSD_4TB/.../src/assistx` tree for this rollback.**
