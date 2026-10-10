# Fleet-hardware advisory preview (read-only, opt-in)

This change adds a **separate authenticated observation endpoint** to AssistX. It does **not** modify auto-router, executor capacity, Neo4j admission, runtime projections, launch/cancellation, tracing writes, leases, or any assignment decision.

## Configure

The fleet hardware evidence repository is private. Its JSON must never be copied into the public `auto-assist` source or test fixtures. On an explicitly trusted AssistX host that already has authorized **read-only** access to that private checkout, configure:

```bash
export ASSISTX_FLEET_HARDWARE_ROOT=/absolute/path/to/private/fleet-hardware
```

The variable is **unset by default**. Without it, the authenticated endpoint returns **503 not configured**. There is no automatic cloning, network fetch, SSH, scheduling, background refresh, or filesystem mutation.

Read an operator preview using existing AssistX Basic auth at:

```text
GET /api/fleet/hardware-preview?ram_gib=20&gpu_vram_gib=16&data_host=nas-node&limit=50
```

The response contains only a bounded per-node summary: node ID, OS-visible RAM total, PCI function IDs satisfying a requested *driver-reported* VRAM threshold, risk **codes**, mount-host relationships by node name, blockers, evidence labels and an integrity reference. It excludes private filesystem paths, disk WWN/serial/UUID, raw hardware JSON and internal IP addresses.

The endpoint is registered with the existing `auth` dependency and accepts GET only. No unauthenticated endpoint exposes this data. Do not expose this operator route through a public proxy without independent access controls.

## Integrity and freshness

- Read `snapshots/LATEST`, `inventory/fleet-resources.json`, `inventory/fleet-topology.json`, `inventory/tailscale-nodes.json`, and the version-matched `probe-status.json`. A changed snapshot pointer during a read, schema/coverage disagreement, missing file, invalid JSON, or unauthorized source state returns HTTP 503.
- Refuse symlinked input files and oversized JSON files.
- A snapshot is **FRESH** only if timestamp age is nonnegative and at most 24 hours. Stale records remain visible for operator review, with `STALE_SNAPSHOT` blockers; they are not candidate approvals.
- Each response has a SHA-256 digest of a canonical manifest of those five parsed input files' raw bytes. This digest is a **preview-specific trace**, not the private fleet-hardware service's full raw-probe digest, an authenticity signature, or an admission token.
- Source node identity and `admission_status` must agree with the probe-status results. Unverified nodes never appear as independently verified.
- One PCI function must satisfy requested reported VRAM. Shared GTT, VRAM from multiple PCI functions, total system RAM and remote mounted filesystems are never summed as available model capacity.
- `data_host` matching indicates direct host identity or a **known host-level remote mount**, not verified file location or a physical connection route.
- Every response sets `admission_allowed=false`, `dispatch_allowed=false`, and every node has `dispatch=DENIED_NO_AUTHORITY`.

## Architecture boundary

```text
private fleet-hardware repo (versioned, read-only export)
          |
          | operator-configured local filesystem path, no network request
          v
AssistX fleet_hardware_preview.py (pure read-only projection)
          |
          v
Authenticated GET /api/fleet/hardware-preview
          |
          v
Operator / UI analysis ONLY -- no dispatch or lease capabilities
```

The actual scheduling/admission system must independently check live utilization, workload/runtime compatibility, exclusive resource reservations, Neo4j transaction closure and credentials/fencing authority **after** deciding if static hardware is worth inspecting. A candidate that appears plausible here is **not admitted**.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_fleet_hardware_preview.py
ruff check src/assistx/fleet_hardware_preview.py src/assistx/api_router.py tests/test_fleet_hardware_preview.py
```

The tests use synthetic node IDs, fake disk paths, and documentation-only IP fixtures. They assert authentication, disabled-by-default behavior, strict read-only methods, no private source disclosures, snapshot consistency, stale sources, unknown nodes, GPU memory accounting, and digest changes.

Full AssistX startup/integration acceptance requires the existing application's full dependencies, startup checks and authenticated reverse-proxy review. This change is **not** a production rollout.

## Possible follow-up

A user interface could show this endpoint in the Fleet Status and trace detail screens. It should link a decision to the preview digest/snapshot and display missing evidence, without silently passing the advisory to routing or execution. Avoid storing full raw private inventories in AssistX public source or public logs.
