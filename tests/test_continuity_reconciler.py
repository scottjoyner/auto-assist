from assistx.continuity_reconciler import reconcile_batch
from assistx.continuity_state import (
    ContinuityConfig,
    InMemoryContinuityStore,
    build_signed_event,
)


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[str] = []
        self.fail = fail

    def commit(self, event):
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.events.append(event["event_id"])


def test_reconciler_commits_and_acknowledges_events():
    store = InMemoryContinuityStore(
        ContinuityConfig("fleet", "beelink", "continuity-secret-123456")
    )
    event = build_signed_event(
        cluster_id="fleet",
        source_node_id="beelink",
        epoch=1,
        kind="task.completed",
        payload={"task_id": "t1"},
        durability="durable",
        secret=store.config.signing_secret,
    )
    store.append_event(event)
    result = reconcile_batch(store, Sink())
    assert result["committed"] == [event["event_id"]]
    assert store.pending_durable_events() == []


def test_reconciler_leaves_event_pending_on_neo4j_failure():
    store = InMemoryContinuityStore(
        ContinuityConfig("fleet", "beelink", "continuity-secret-123456")
    )
    event = build_signed_event(
        cluster_id="fleet",
        source_node_id="beelink",
        epoch=1,
        kind="task.completed",
        payload={"task_id": "t1"},
        durability="durable",
        secret=store.config.signing_secret,
    )
    store.append_event(event)
    result = reconcile_batch(store, Sink(fail=True))
    assert result["failed"][0]["event_id"] == event["event_id"]
    assert len(store.pending_durable_events()) == 1
