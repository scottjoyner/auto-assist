"""R37 regression: absent tailnet-discovery evidence must read as *not* discovered.

`fleet_context_projection` selects discoverable nodes with
``coalesce(n.tailnet_discovered, false) = true`` — i.e. a missing/null fact is
NOT a discovery claim.  `fleet_routing_matrix.validate_matrix` (and therefore
`import_matrix`, which persists the normalized row into Neo4j) used to default
the same field to **True**, so a document that simply omitted the field was
written back as an affirmative discovery claim.  Every other verdict boolean
in that validator already defaults to False.

This module is hermetic: pure in-process dicts, no DB, HTTP, Neo4j, docker,
socket, credential or network-mount access.
"""

from __future__ import annotations

from assistx.fleet_routing_matrix import matrix_fingerprint, validate_matrix


def _node(**overrides):
    node = {"node_id": "x1-370"}
    node.update(overrides)
    return node


def _matrix(node):
    return {
        "schema_version": "fleet_routing_matrix.v1",
        "generated_at_utc": "2026-11-27T00:00:00Z",
        "summary": {"tailnet_nodes": 1},
        "policy": {"discovery_is_not_admission": True},
        "nodes": [node],
        "profiles": [],
        "rankings": {},
    }


def _normalized(node):
    return validate_matrix(_matrix(node))["nodes"][0]


def test_missing_tailnet_discovered_fails_closed() -> None:
    """The defect: an omitted field was silently upgraded to a discovery claim."""
    assert _normalized(_node())["tailnet_discovered"] is False


def test_explicit_tailnet_discovered_true_is_preserved() -> None:
    assert _normalized(_node(tailnet_discovered=True))["tailnet_discovered"] is True


def test_explicit_tailnet_discovered_false_is_preserved() -> None:
    assert _normalized(_node(tailnet_discovered=False))["tailnet_discovered"] is False


def test_absent_discovery_fingerprint_matches_false_not_true() -> None:
    """The persisted fingerprint must record absence as False, never as True."""
    absent = matrix_fingerprint(validate_matrix(_matrix(_node())))
    explicit_false = matrix_fingerprint(
        validate_matrix(_matrix(_node(tailnet_discovered=False)))
    )
    explicit_true = matrix_fingerprint(
        validate_matrix(_matrix(_node(tailnet_discovered=True)))
    )
    assert absent == explicit_false
    assert absent != explicit_true


def test_all_missing_verdict_booleans_default_closed() -> None:
    row = _normalized(_node())
    for field in (
        "tailnet_discovered",
        "online",
        "allow_agent_runtime",
        "allow_code_execution",
    ):
        assert row[field] is False, field
