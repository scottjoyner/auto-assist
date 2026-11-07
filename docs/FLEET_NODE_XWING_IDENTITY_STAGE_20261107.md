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

## Guarded one-shot launcher (installed into private xwing stage only)

The research branch provides `scripts/one_shot_identity_gate.py` as a
**non-admitting, source-pinned one-shot launcher**. On November 7 it was
installed at `xwing` inside the separate owner-only stage directory, checksum
`d6a1bb1ef1e932ddf0611c7ea105533d59a528c2f02f5732bcb1c4c00985cf1a`.
It was **not** activated by systemd, granted a real node credential, or used
against the production AssistX host; no worker process was started.

The launcher requires the stage directory to be owner-only, verifies exact
SHA-256 pins for all three source modules, rejects sidecar code, symlinks and
unapproved origins, sanitizes the child environment to remove operator Basic
and provider credentials, and invokes **only** `--identity-probe-only`.
Without `--identity-only` it checks the staged source without making HTTP;
with `--identity-only` it still exits **2** even after a positive identity
response. Tests use a synthetic loopback server and fake credentials.

The launcher is not an immutable or remotely attested image and cannot close
races with concurrent same-UID source replacement. Physical one-node adoption
requires independent custody, a pinned approved origin, operator-owned node
principal, rollback evidence and a fresh signed resource authority before
any productive execution. The five legacy Task routes must remain 403 to
machine callers regardless of identity-only acceptance.

## Physical guarded launcher acceptance — 2026-11-07

The first **default invocation** of the guarded launcher correctly returned
`SOURCE_HASH_OR_CUSTODY_MISMATCH`, `source_verified=false`, exit 2. The reason
was an untrusted `__pycache__` containing three `cpython-314.pyc` files,
generated by the earlier explicit `python3 -m py_compile` command. The Python
flag `PYTHONDONTWRITEBYTECODE=1` does **not** suppress `py_compile` output.

After inspecting ownership, paths, modes and the three pinned source hashes,
the compiled files were **moved intact**, not deleted, to xwing's private
`~/.local/state/assistx-staging-custody/63ce16f02d1a11be347f1cd83162e7dcf0183494-pycompile-quarantine`
for independent examination. No production cache was modified.

Post-quarantine, the default launcher returned `source_verified=true`,
`SOURCE_PINNED_NO_ADMISSION`, `task_admission=HOLD`, exit **2**.
A separate *synthetic loopback* positive test of the installed launcher
returned `source_verified=true`, `identity_verified=true`,
`production_admission_permitted=false`, `task_admission=HOLD`, exit **2**.
The test server observed exactly one identity GET, a fake token/ID, and **no**
Task call or operator Basic header (even when synthetic Basic poison values
were present in the parent's environment). The five active services and
legacy shared source tree remained untouched; the xwing user service was
independently confirmed active after the test.

CAUTION: do not run `py_compile` directly in the pinned stage; it creates
bytecode despite `PYTHONDONTWRITEBYTECODE`. Compile an extracted copy in a
separate scratch path or use read-only `ast.parse` for source syntax checks.
