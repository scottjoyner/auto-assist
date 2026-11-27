"""Hermetic contract tests for static-vs-discovered fleet node merge precedence.

Synthetic in-memory node dictionaries only: no graph, router, DB, service,
credential, clock or network access.  Static nodes are operator-declared
projection rows; tailnet-discovered rows may enrich them but must not silently
relabel or unblock an operator-classified node.
"""

from __future__ import annotations

from assistx.fleet_context_projection import merge_nodes


def _static(**overrides):
    row = {
        "node_id": "node-a",
        "display_name": "Operator declared",
        "lane": "paperclip",
        "local": True,
        "can_use_free_api": False,
        "blocked": True,
        "running": False,
        "allow_code_execution": False,
        "capabilities": ["context_projection"],
        "services": [{"name": "static.service", "url": "http://static/service"}],
        "metadata": {"origin": "operator"},
    }
    row.update(overrides)
    return row


def _discovered(**overrides):
    row = {
        "node_id": "node-a",
        "display_name": "Tailnet discovered",
        "lane": "local",
        "local": True,
        "can_use_free_api": True,
        "blocked": False,
        "running": True,
        "allow_code_execution": True,
        "capabilities": ["terminal", "context_projection"],
        "services": [{"name": "discovered.service", "url": "http://tailnet/service"}],
        "metadata": {"origin": "tailnet", "tailnet_discovered": True},
    }
    row.update(overrides)
    return row


def _merged_row(static, discovered, node_id="node-a"):
    rows = {row["node_id"]: row for row in merge_nodes([static], [discovered])}
    return rows[node_id]


def test_discovered_evidence_cannot_relabel_operator_lane():
    row = _merged_row(_static(), _discovered())
    assert row["lane"] == "paperclip"


def test_discovered_evidence_cannot_unblock_operator_node():
    row = _merged_row(_static(), _discovered())
    assert row["blocked"] is True


def test_discovered_evidence_cannot_grant_code_execution():
    row = _merged_row(_static(), _discovered())
    assert row["allow_code_execution"] is False


def test_discovered_evidence_cannot_grant_free_api_use():
    row = _merged_row(_static(), _discovered())
    assert row["can_use_free_api"] is False


def test_discovered_evidence_cannot_flip_locality():
    row = _merged_row(_static(local=False), _discovered(local=True))
    assert row["local"] is False


def test_discovered_evidence_cannot_replace_operator_metadata():
    row = _merged_row(_static(), _discovered())
    assert row["metadata"] == {"origin": "operator"}


def test_operator_free_api_lane_survives_discovery():
    static = _static(lane="free_api", local=False, can_use_free_api=True, blocked=False)
    row = _merged_row(static, _discovered(lane="local", can_use_free_api=False))
    assert row["lane"] == "free_api"
    assert row["local"] is False
    assert row["can_use_free_api"] is True


def test_discovery_enriches_missing_display_fields():
    static = _static()
    del static["display_name"]
    static.pop("detail", None)
    discovered = _discovered(detail="tailnet worker")
    row = _merged_row(static, discovered)
    assert row["display_name"] == "Tailnet discovered"
    assert row["detail"] == "tailnet worker"


def test_capabilities_are_unioned_and_sorted():
    row = _merged_row(_static(), _discovered())
    assert row["capabilities"] == ["context_projection", "terminal"]


def test_services_keep_static_endpoint_and_append_discovered():
    row = _merged_row(_static(), _discovered())
    assert [service["name"] for service in row["services"]] == [
        "static.service",
        "discovered.service",
    ]


def test_running_remains_observational_or():
    assert _merged_row(_static(running=False), _discovered(running=True))["running"] is True
    assert _merged_row(_static(running=True), _discovered(running=False))["running"] is True
    assert _merged_row(_static(running=False), _discovered(running=False))["running"] is False


def test_uncollided_discovered_row_is_not_rewritten():
    static = _static(node_id="node-a")
    discovered = _discovered(node_id="node-b")
    rows = {row["node_id"]: row for row in merge_nodes([static], [discovered])}
    assert rows["node-b"]["lane"] == "local"
    assert rows["node-b"]["blocked"] is False
    assert rows["node-b"]["running"] is True


def test_duplicate_node_ids_do_not_multiply_rows():
    merged = merge_nodes([_static(), _static(node_id="node-b")], [_discovered()])
    assert [row["node_id"] for row in merged] == ["node-a", "node-b"]
