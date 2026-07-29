from assistx.self_healing import SelfHealingController


class Result:
    def __init__(self, row=None):
        self.row = row

    def consume(self):
        return None

    def single(self):
        return self.row


class Session:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []

    def run(self, query, params):
        self.calls.append((query, params))
        if "RETURN i.node_id AS node_id" in query:
            return Result({"node_id": "x1"})
        if "RETURN n.node_id AS node_id" in query:
            return Result({"node_id": params["node_id"]})
        return Result()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class Neo:
    def __init__(self):
        self.session = Session()

    def _session(self):
        return self.session


def test_auto_quarantine_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSISTX_AUTO_QUARANTINE_ENABLED", raising=False)
    controller = SelfHealingController()
    result = controller.quarantine(Neo(), "x1:offline:node", "self-healing-controller")
    assert result["blocked"] is True


def test_operator_can_quarantine_critical_incident(monkeypatch):
    controller = SelfHealingController()
    result = controller.quarantine(Neo(), "x1:offline:node", "operator")
    assert result["quarantined"] is True


def test_rejoin_requires_clear_critical_health_evidence():
    controller = SelfHealingController()
    neo = Neo()
    blocked = controller.rejoin(
        neo, "x1", "operator",
        {"incidents": [{"node_id": "x1", "severity": "critical"}]},
    )
    assert blocked["blocked"] is True
    clear = controller.rejoin(neo, "x1", "operator", {"incidents": []})
    assert clear["rejoined"] is True
