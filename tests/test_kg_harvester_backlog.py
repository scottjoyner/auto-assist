from __future__ import annotations

from unittest.mock import Mock

from assistx import kg_harvester


def _harvester_without_database() -> kg_harvester.KgInsightHarvester:
    harvester = kg_harvester.KgInsightHarvester.__new__(
        kg_harvester.KgInsightHarvester
    )
    harvester._batch = []
    harvester._cycle_limit = kg_harvester.MAX_TASKS_PER_CYCLE
    return harvester


def test_harvester_skips_when_ready_llm_backlog_is_healthy(monkeypatch) -> None:
    monkeypatch.setattr(kg_harvester, "READY_THRESHOLD", 5)
    monkeypatch.setattr(kg_harvester, "TARGET_BACKLOG", 10)
    monkeypatch.setattr(kg_harvester, "MAX_TASKS_PER_CYCLE", 3)

    harvester = _harvester_without_database()
    harvester._ready_llm_count = Mock(return_value=5)
    harvester.harvest_cycle = Mock(return_value=3)

    assert harvester.harvest_until_target() == 0
    harvester.harvest_cycle.assert_not_called()


def test_harvester_limits_refill_to_remaining_target_capacity(monkeypatch) -> None:
    monkeypatch.setattr(kg_harvester, "READY_THRESHOLD", 5)
    monkeypatch.setattr(kg_harvester, "TARGET_BACKLOG", 3)
    monkeypatch.setattr(kg_harvester, "MAX_TASKS_PER_CYCLE", 10)

    harvester = _harvester_without_database()
    harvester._ready_llm_count = Mock(return_value=1)
    harvester.harvest_cycle = Mock(return_value=2)

    assert harvester.harvest_until_target() == 2
    assert harvester._cycle_limit == 2
    harvester.harvest_cycle.assert_called_once_with()


def test_task_buffer_cannot_exceed_cycle_limit() -> None:
    harvester = _harvester_without_database()
    harvester._cycle_limit = 1

    for index in range(2):
        harvester._create_llm_task(
            title=f"task-{index}",
            messages=[{"role": "user", "content": "analyze"}],
            idempotency_key=f"task-{index}",
        )

    assert len(harvester._batch) == 1
    assert harvester._batch[0]["title"] == "task-0"
