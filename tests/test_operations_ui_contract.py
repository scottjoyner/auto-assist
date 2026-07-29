from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_operations_workspace_exposes_core_operator_flows():
    template = (ROOT / "templates" / "operations.html").read_text()

    for contract in (
        "Attention queue",
        "Task management",
        "Nodes and loaded models",
        "Improvement proposals",
        "/api/fleet/self-healing/reconcile",
        "/api/tasks",
        "approve-proposal",
        "/api/fleet/diagnoses/",
        "/api/fleet/recovery-control/proposals",
        "allocation-list",
        "operations-readiness",
        "allocation-release",
        "node-maintenance",
        "node-quarantine",
        "Download evidence",
        "Controller leadership",
        'id="controller-list"',
        "refreshFailures",
    ):
        assert contract in template


def test_operations_workspace_has_dedicated_responsive_styles():
    styles = (ROOT / "static" / "css" / "operations.css").read_text()

    assert ".ops-overview" in styles
    assert ".ops-node-grid" in styles
    assert "@media (max-width: 560px)" in styles
