# Read-only fleet node source custody — 2026-11-06

**Scope:** source-only research evidence for Auto-Assist P0 issue #501 and denial-based draft PRs #550, #551, and #555. This branch does not implement Task authentication, change agent code, register credentials, or authorize machine work.

## Reproducible local audit (no deployment)

From the authorized fleet hub, stream the source-census script over existing authorized SSH to an approved node:

    ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes NODE python3 -B - < scripts/fleet_node_source_census.py

The source is delivered by standard input, never installed on that node. The script uses standard-library process inspection and AST parsing. It does not print command arguments, inspect environment variables, open credential files, contact APIs, import or execute the loader, change services, or write files. It refuses unknown launcher contracts, unsupported roots, unexpected arguments, linked source leaves, and unstable file reads.

**Every successful result is non-admitting:** task_admission=HOLD, loaded_module_identity_proven=false, decision=HOLD_SOURCE_AND_RUNTIME_NOT_ATTESTED. A SHA-256 of an on-disk Python file does not prove that those same objects are loaded in a long-running Python process.

## November 6 physical observation

Read-only SSH and systemd-user observations reached xwing, Deathstar, Destroyer, Lenovo, and OptiPlex. All five had active user-level fleet-node-agent processes. They launched standalone run_fleet_node_agent.py scripts referencing older fleet_node_agent.py code with an obsolete state=READY poll literal and a static empty DEFAULT_CAPS override.

The referenced agent source currently has identical on-disk SHA-256 across five hosts, but file modification timestamps on Deathstar and Lenovo are newer than those processes. Lenovo's running Python executable is marked (deleted). These findings **increase uncertainty about loaded code** and do not authorize restarts. Launcher hashes differ across nodes. Strict read-only audit results: five valid HOLD records.

Detailed per-node JSON evidence remains owner-only on x1-370 under /home/scott/.local/state/fleet-source-custody/, intentionally uncommitted.

## Gates before a one-node stage

- Pin the actual immutable source/image and approve a named staging node, rollback owner, and machine principal.
- Review the five legacy Task HTTP 403 guards from PR #555 and identity-only nonadmitting probe. Do not revive closed/superseded PR #556 or copy operator Basic secrets.
- Verify approved origin, node identity, transport constraints, replay and cross-node denial, no graph mutation, and the retained task_admission=HOLD witness.
- Require fresh signed runtime projection and resource-issued ownership/COMMIT evidence separately before productive dispatch.
- Never equate positive identity, green synthetic CI, an on-disk SHA, or an active service with admitted machine work.

## Validation

Ten synthetic tests passed both locally on x1-370 and in a cached, network-disabled, read-only, capability-dropped, resource-limited Docker runtime. The dedicated source-custody workflow exercises this synthetic contract, not a production admission path.
