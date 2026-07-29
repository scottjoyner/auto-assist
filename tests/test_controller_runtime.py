from __future__ import annotations

from typing import Any

from assistx.controller_runtime import DurableController


class MemoryStore:
    def __init__(self) -> None:
        self.lease: dict[str, Any] | None = None
        self.checkpoint: dict[str, Any] = {}

    def acquire(self, controller_id, instance_id, *, now_ms, ttl_ms):
        lease = self.lease
        if (
            lease
            and lease["owner_instance_id"] != instance_id
            and lease["expires_at_ts"] > now_ms
        ):
            return None
        owner_changed = not lease or lease["owner_instance_id"] != instance_id
        token = int(lease["fencing_token"] if lease else 0)
        self.lease = {
            "controller_id": controller_id,
            "owner_instance_id": instance_id,
            "fencing_token": token + 1 if owner_changed else token,
            "expires_at_ts": now_ms + ttl_ms,
        }
        return dict(self.lease)

    def begin_tick(
        self, controller_id, instance_id, fencing_token, tick_key, *, now_ms
    ):
        if not self._valid(instance_id, fencing_token, now_ms):
            return {"started": False, "reason": "replayed_or_in_progress"}
        if self.checkpoint.get("last_completed_tick_key") == tick_key:
            return {"started": False, "reason": "replayed_or_in_progress"}
        if (
            self.checkpoint.get("status") == "RUNNING"
            and self.checkpoint.get("fencing_token") == fencing_token
        ):
            return {"started": False, "reason": "replayed_or_in_progress"}
        self.checkpoint = {
            "controller_id": controller_id,
            "owner_instance_id": instance_id,
            "fencing_token": fencing_token,
            "tick_key": tick_key,
            "status": "RUNNING",
        }
        return {"started": True, "checkpoint": dict(self.checkpoint)}

    def finish_tick(
        self,
        controller_id,
        instance_id,
        fencing_token,
        tick_key,
        *,
        now_ms,
        status,
        result,
    ):
        if not self._valid(instance_id, fencing_token, now_ms):
            return False
        if (
            self.checkpoint.get("owner_instance_id") != instance_id
            or self.checkpoint.get("fencing_token") != fencing_token
            or self.checkpoint.get("tick_key") != tick_key
        ):
            return False
        self.checkpoint.update(status=status, result=result)
        if status == "SUCCEEDED":
            self.checkpoint["last_completed_tick_key"] = tick_key
        return True

    def _valid(self, instance_id, fencing_token, now_ms):
        return bool(
            self.lease
            and self.lease["owner_instance_id"] == instance_id
            and self.lease["fencing_token"] == fencing_token
            and self.lease["expires_at_ts"] > now_ms
        )


def runtime(store, instance, clock):
    return DurableController(
        "recovery",
        lambda: (store, lambda: None),
        instance_id=instance,
        lease_seconds=30,
        clock=lambda: clock[0],
    )


def test_only_lease_owner_executes_and_replay_is_suppressed():
    store, clock, calls = MemoryStore(), [100.0], []
    leader = runtime(store, "instance-a", clock)
    standby = runtime(store, "instance-b", clock)

    first = leader.run_tick("tick-1", lambda: calls.append("run") or {"count": 1})
    replay = leader.run_tick("tick-1", lambda: calls.append("duplicate") or {})
    blocked = standby.run_tick("tick-2", lambda: calls.append("standby") or {})

    assert first["ok"] is True
    assert replay["reason"] == "replayed_or_in_progress"
    assert blocked["reason"] == "standby_not_leader"
    assert calls == ["run"]


def test_expired_leader_fails_over_with_higher_fencing_token():
    store, clock = MemoryStore(), [100.0]
    first = runtime(store, "instance-a", clock).run_tick("tick-1", lambda: {})
    clock[0] = 131.0
    second = runtime(store, "instance-b", clock).run_tick("tick-2", lambda: {})

    assert first["fencing_token"] == 1
    assert second["ok"] is True
    assert second["fencing_token"] == 2


def test_stale_leader_cannot_commit_after_lease_loss():
    store, clock = MemoryStore(), [100.0]
    leader = runtime(store, "instance-a", clock)

    def lose_lease():
        clock[0] = 131.0
        runtime(store, "instance-b", clock).run_tick("tick-2", lambda: {})
        return {"unsafe": "stale result"}

    result = leader.run_tick("tick-1", lose_lease)

    assert result["ok"] is False
    assert result["reason"] == "leadership_lost_before_commit"
    assert store.checkpoint["last_completed_tick_key"] == "tick-2"


def test_failed_work_is_durably_checkpointed_and_can_retry():
    store, clock = MemoryStore(), [100.0]
    controller = runtime(store, "instance-a", clock)

    failed = controller.run_tick(
        "tick-1",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    retried = controller.run_tick("tick-1", lambda: {"reconciled": 2})

    assert failed["ok"] is False
    assert failed["checkpointed"] is True
    assert store.checkpoint["status"] == "SUCCEEDED"
    assert retried["ok"] is True
