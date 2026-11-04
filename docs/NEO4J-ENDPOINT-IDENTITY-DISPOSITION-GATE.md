# ServiceEndpoint duplicate: private evidence-disposition gate (research v1)

**DRAFT / READ-ONLY OFFLINE REVIEW / NEVER A MIGRATION / PRODUCTION NO-GO.**

This pure standard-library Python tool checks a *private structural review packet*
against two separate read-only aggregate receipts previously collected from an
image-pinned AssistX container. It has no Neo4j driver, Docker/SSH/network
calls, writer or schema DDL. The parser opens no files by default.
All input files must be explicitly supplied and be single-link regular,
owner-owned and non-group/world-readable (mode 0600), and at most 128 KiB.
Symlinks, duplicate JSON keys, unexpected fields and missing evidence fail closed.

Private property-key names and source receipts are **not** committed or uploaded.
All GitHub test fixtures use synthetic names such as synthetic_common_00.
Standard output contains only aggregate counts and fixed reason codes, never
property names, node IDs, endpoint IDs, paths, credentials or values.

## Exact v1 boundary — November 3, 2026 (US Eastern)

Fixed read-only inspection showed **two** colliding records with 9 and 17
property keys, seven shared key names, one shared SwarmNode owner, an incoming
EXPOSES edge per node and an outgoing SERVES edge on the ModelEndpoint subtype.
There are 26 node-property review entries. The owner-private disposition packet
leaves every property and relationship UNREVIEWED_HOLD with no survivor selected,
no property value read and no release authority. The evidence does not prove
value-level provenance, downstream consumers, operator approval, independently
witnessed clock or transaction consistency between the two snapshots.

## Operator use (private offline files only)

The default command returns exit 2, HOLD_PRIVATE_EVIDENCE_REQUIRED:

    python3 scripts/endpoint_identity_disposition_gate.py

When an authorized operator controls all three private local evidence files
with owner-only 0600 permissions, the read-only check can be invoked with:

    python3 scripts/endpoint_identity_disposition_gate.py \
      --private-review "$PRIVATE_REVIEW_PATH" \
      --private-shape "$PRIVATE_SHAPE_RECEIPT_PATH" \
      --private-owners "$PRIVATE_OWNER_RECEIPT_PATH"

Exit **2** means HOLD_PRIVATE_EVIDENCE_REQUIRED or HOLD_REVIEW_REQUIRED:
the receipts may be structurally consistent, but **NO EXECUTION IS AUTHORIZED**.
Exit **3** means DENY_EVIDENCE_DRIFT with a fixed non-sensitive reason code.
There is no success / release-GO exit code.

## Strict scope and security limitations

The checker compares exact input schemas, property counts and names, cross-node
overlap, edge direction/type/neighbor labels and edge-property keys, owner/model
relationship counts, and SHA-256 linkage back to both locally held read-only
receipts. Missing, substituted or duplicated fields, extra edges, unknown
neighbor labels, claimed approval/review completion, survivor selection,
symlink or group/world-accessible evidence files fail closed.

These hashes are **self-administered consistency checks**, not independent
external custody. An administrator controlling all three files can rewrite
their contents and receipts together and still receive only HOLD. Do not
promote a structural match to a delete/merge proposal. If the underlying graph
changes, obtain an independently authorized fresh read-only snapshot, perform
fresh human review and update this strict pinned-v1 policy explicitly.

Before any live reconciliation, an authorized independent reviewer must approve
individual field/value/provenance disposition, preserve every inbound/outbound
consumer reference, review all relationship properties, rehearse reversal against
an exact data-shaped disposable copy, prove writer quiescence, restrict runtime
roles separately from schema bootstrap, and validate database-level denial and
independent external custody. This tool performs none of those privileged steps.

## Synthetic regression

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q \
      tests/test_endpoint_identity_disposition_gate.py

The suite covers property omissions/substitutions/duplicates, forged release and
owner approval, consumer-review claims, unknown relationships and neighbor labels,
source digest drift, file privacy and symlink checks, JSON key duplication, and
an intentionally synchronized same-administrator rewrite negative control.

Relevant blockers: Auto-Assist #322 (collision), #318 (runtime privileges),
SensorGraft #51 (independent custody). Keep this draft unmerged and **NO-GO**.
