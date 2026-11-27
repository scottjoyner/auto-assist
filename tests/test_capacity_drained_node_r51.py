"""Round 51 — a deliberate zero capacity must not be re-credited.

The routing-matrix producer (``fleet_routing_matrix.py``) normalizes an
operator-drained node to ``max_concurrent=0`` via ``max(0, ...)``.  The forecast
consumer used ``max(1, int(node.get("max_concurrent") or ... or 1))``, which
turned that deliberate zero back into a phantom slot and could admit batch
work into capacity that does not exist.  All cases below are pure dict-in /
dict-out: no DB, socket, service or filesystem.
"""

from assistx.capacity_forecast import build_capacity_forecast


def test_explicit_zero_capacity_not_recredited():
    result = build_capacity_forecast(
        [],
        [{"hostname": "drained", "online": True, "max_concurrent": 0}],
        {},
    )
    assert result["summary"]["online_capacity"] == 0
    assert result["summary"]["available_slots"] == 0


def test_fully_drained_fleet_reports_no_capacity_bottleneck():
    result = build_capacity_forecast(
        [{"id": "u", "status": "READY", "payload": {"queue_class": "interactive"}}],
        [
            {"hostname": "a", "online": True, "max_concurrent": 0},
            {"hostname": "b", "online": True, "max_concurrent": 0},
        ],
        {"entries": [{"loaded": True, "online": True, "tokens_per_second": 10}]},
    )
    assert result["summary"]["online_capacity"] == 0
    assert "no online execution capacity" in result["bottlenecks"]


def test_batch_admission_flips_when_phantom_slot_removed():
    result = build_capacity_forecast(
        [{"id": "u", "status": "READY", "payload": {"queue_class": "interactive"}}],
        [
            {"hostname": "live", "online": True, "max_concurrent": 1},
            {"hostname": "drained", "online": True, "max_concurrent": 0},
        ],
        {},
    )
    assert result["summary"]["online_capacity"] == 1
    assert result["summary"]["available_slots"] == 1
    # The only real slot is already owed to the urgent interactive task.
    assert result["admission_policy"]["batch_admission_allowed"] is False


def test_malformed_capacity_is_fail_closed_without_crash():
    for bad in ("oops", True, -3, float("nan"), float("inf"), "5.5"):
        result = build_capacity_forecast(
            [],
            [{"hostname": "n", "online": True, "max_concurrent": bad}],
            {},
        )
        assert result["summary"]["online_capacity"] == 0, bad


def test_absent_capacity_keeps_historical_fallback():
    result = build_capacity_forecast(
        [],
        [
            {"hostname": "w", "online": True, "weight": 3},
            {"hostname": "d", "online": True},
        ],
        {},
    )
    assert result["summary"]["online_capacity"] == 4
