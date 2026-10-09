#!/usr/bin/env python3
"""Bounded, metadata-only live watcher for AssistX trace timelines.

This client intentionally refuses the legacy full-detail endpoint and never
requests payload previews. It is an operator diagnostic, not custody evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable

EVENT_FIELDS = (
    "event_id",
    "ts_ms",
    "event_type",
    "source",
    "task_id",
    "dispatch_id",
    "route_id",
    "assignment_id",
)
PAGE_SCHEMA = "trace-event-page-v1"
EVENT_SCHEMA = "trace-live-event-v1"
HEALTH_SCHEMA = "trace-live-health-v1"
STATE_SCHEMA = "trace-live-watch-state-v1"


class TimelineError(RuntimeError):
    """Base watcher failure."""


class TimelineHttpError(TimelineError):
    def __init__(self, status: int, reason: str):
        super().__init__(f"HTTP {status}: {reason}")
        self.status = status


class SeenIds:
    def __init__(self, cap: int, initial: list[str] | None = None):
        if cap < 1:
            raise ValueError("seen-ID cap must be positive")
        self.cap = cap
        self._order: deque[str] = deque()
        self._set: set[str] = set()
        for event_id in initial or []:
            self.add(event_id)

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._set
    def add(self, event_id: str) -> None:
        if event_id in self._set:
            return
        self._order.append(event_id)
        self._set.add(event_id)
        while len(self._order) > self.cap:
            old = self._order.popleft()
            self._set.discard(old)

    def as_list(self) -> list[str]:
        return list(self._order)


class TailState:
    def __init__(
        self,
        correlation_id: str,
        max_seen: int,
        *,
        seen: list[str] | None = None,
        latest_ts_ms: int | None = None,
        bootstrapped: bool = False,
    ):
        self.correlation_id = correlation_id
        self.seen = SeenIds(max_seen, seen)
        self.latest_ts_ms = latest_ts_ms
        self.bootstrapped = bootstrapped
        self.polls = 0
def _clean_event(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TimelineError("timeline event is not an object")
    event_id = raw.get("event_id")
    ts_ms = raw.get("ts_ms")
    if not isinstance(event_id, str) or not event_id or len(event_id) > 320:
        raise TimelineError("invalid timeline event_id")
    if type(ts_ms) is not int or ts_ms < 0:
        raise TimelineError("invalid timeline ts_ms")
    # Deliberate allowlist: payload-shaped fields are dropped even if a server
    # regression accidentally includes them.
    return {field: raw.get(field) for field in EVENT_FIELDS}


def _validate_page(page: Any, correlation_id: str) -> dict[str, Any]:
    if not isinstance(page, dict) or page.get("schema") != PAGE_SCHEMA:
        raise TimelineError("unexpected trace page schema")
    if page.get("correlation_id") != correlation_id:
        raise TimelineError("trace page correlation_id mismatch")
    if page.get("metadata_only") is not True:
        raise TimelineError("trace page is not metadata-only")
    events = page.get("events")
    if not isinstance(events, list) or len(events) > 100:
        raise TimelineError("invalid trace event page")
    next_cursor = page.get("next_cursor")
    if next_cursor is not None and (
        not isinstance(next_cursor, str) or len(next_cursor) > 720
    ):
        raise TimelineError("invalid trace next_cursor")
    clean = [_clean_event(event) for event in events]
    return {
        "events": clean,
        "has_more": page.get("has_more") is True,
        "next_cursor": next_cursor,
    }


def collect_poll(
    fetch_page: Callable[[str | None], dict[str, Any]],
    state: TailState,
    *,
    max_pages: int,
    emit_existing: bool = False,
) -> dict[str, Any]:
    """Collect new metadata until known history or the page budget is reached."""
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    cursor: str | None = None
    collected_desc: list[dict[str, Any]] = []
    poll_ids: set[str] = set()
    pages = 0
    reached_known = False
    last_has_more = False

    while pages < max_pages:
        page = _validate_page(fetch_page(cursor), state.correlation_id)
        pages += 1
        events = page["events"]
        if not state.bootstrapped:
            for event in events:
                state.seen.add(event["event_id"])
                state.latest_ts_ms = max(
                    state.latest_ts_ms or 0, event["ts_ms"]
                )
            state.bootstrapped = True
            emitted = list(reversed(events)) if emit_existing else []
            return {
                "events": emitted,
                "pages": pages,
                "reached_known": bool(events),
                "gap_possible": False,
            }

        for event in events:
            event_id = event["event_id"]
            if event_id in state.seen:
                reached_known = True
                break
            if event_id in poll_ids:
                continue
            poll_ids.add(event_id)
            collected_desc.append(event)

        if reached_known:
            break
        last_has_more = page["has_more"]
        if not page["has_more"] or not page["next_cursor"]:
            break
        cursor = str(page["next_cursor"])
    gap_possible = (
        pages >= max_pages and not reached_known and last_has_more
    )
    emitted = list(reversed(collected_desc))
    for event in emitted:
        state.seen.add(event["event_id"])
        state.latest_ts_ms = max(state.latest_ts_ms or 0, event["ts_ms"])

    return {
        "events": emitted,
        "pages": pages,
        "reached_known": reached_known,
        "gap_possible": gap_possible,
    }


def load_state(path: Path, correlation_id: str, max_seen: int) -> TailState:
    if not path.exists():
        return TailState(correlation_id, max_seen)
    obj = json.loads(path.read_text())
    if obj.get("schema") != STATE_SCHEMA:
        raise TimelineError("unexpected watcher state schema")
    if obj.get("correlation_id") != correlation_id:
        raise TimelineError("watcher state belongs to another correlation_id")
    seen = obj.get("seen_event_ids")
    if not isinstance(seen, list) or not all(isinstance(x, str) for x in seen):
        raise TimelineError("invalid watcher state seen_event_ids")
    latest = obj.get("latest_ts_ms")
    if latest is not None and (type(latest) is not int or latest < 0):
        raise TimelineError("invalid watcher state latest_ts_ms")
    return TailState(
        correlation_id,
        max_seen,
        seen=seen,
        latest_ts_ms=latest,
        bootstrapped=bool(obj.get("bootstrapped")),
    )


def save_state(path: Path, state: TailState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "schema": STATE_SCHEMA,
        "correlation_id": state.correlation_id,
        "bootstrapped": state.bootstrapped,
        "latest_ts_ms": state.latest_ts_ms,
        "seen_event_ids": state.seen.as_list(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True) + "\n")
    tmp.replace(path)



class TimelineClient:
    def __init__(
        self,
        base_url: str,
        correlation_id: str,
        *,
        limit: int,
        timeout: float,
    ):
        self.base_url = base_url.rstrip("/")
        self.correlation_id = correlation_id
        self.limit = limit
        self.timeout = timeout

    def fetch(self, cursor: str | None) -> dict[str, Any]:
        cid = urllib.parse.quote(self.correlation_id, safe="")
        query = {"limit": str(self.limit)}
        if cursor:
            query["cursor"] = cursor
        url = (
            f"{self.base_url}/api/traces/{cid}/timeline?"
            + urllib.parse.urlencode(query)
        )
        request = urllib.request.Request(
            url, headers={"Accept": "application/json"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise TimelineHttpError(exc.code, exc.reason or "request failed") from exc
        except urllib.error.URLError as exc:
            raise TimelineError(f"timeline request failed: {exc.reason}") from exc


def _emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, sort_keys=True, separators=(",", ":")), flush=True)


def _event_record(correlation_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": EVENT_SCHEMA,
        "correlation_id": correlation_id,
        **{field: event.get(field) for field in EVENT_FIELDS},
    }


def _health_record(
    state: TailState,
    result: dict[str, Any] | None,
    *,
    stale_after_s: float,
    error: str | None = None,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    lag_ms = (
        max(0, now_ms - state.latest_ts_ms)
        if state.latest_ts_ms is not None
        else None
    )
    return {
        "schema": HEALTH_SCHEMA,
        "correlation_id": state.correlation_id,
        "poll": state.polls,
        "pages_read": result.get("pages") if result else 0,
        "new_events": len(result.get("events", [])) if result else 0,
        "gap_possible": bool(result and result.get("gap_possible")),
        "reached_known_history": bool(result and result.get("reached_known")),
        "latest_ts_ms": state.latest_ts_ms,
        "lag_ms": lag_ms,
        "stale": lag_ms is not None and lag_ms > stale_after_s * 1000,
        "metadata_only": True,
        "historical_retention_proven": False,
        "physical_node_identity_verified": False,
        "error": error,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--max-pages-per-poll", type=int, default=4)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--stale-after", type=float, default=60.0)
    parser.add_argument("--max-seen", type=int, default=5000)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--emit-existing", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health-every-polls", type=int, default=15)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 100:
        raise SystemExit("--limit must be 1..100")
    if args.max_pages_per_poll < 1:
        raise SystemExit("--max-pages-per-poll must be positive")
    if args.interval < 0.1 or args.timeout <= 0 or args.stale_after < 0:
        raise SystemExit("invalid timing argument")
    if args.health_every_polls < 1:
        raise SystemExit("--health-every-polls must be positive")

    try:
        state = (
            load_state(args.state_file, args.correlation_id, args.max_seen)
            if args.state_file
            else TailState(args.correlation_id, args.max_seen)
        )
    except (OSError, ValueError, json.JSONDecodeError, TimelineError) as exc:
        print(f"live-trace-watch: {exc}", file=sys.stderr)
        return 2

    client = TimelineClient(
        args.base_url,
        args.correlation_id,
        limit=args.limit,
        timeout=args.timeout,
    )
    consecutive_errors = 0

    while True:
        state.polls += 1
        try:
            result = collect_poll(
                client.fetch,
                state,
                max_pages=args.max_pages_per_poll,
                emit_existing=args.emit_existing,
            )
            consecutive_errors = 0
            for event in result["events"]:
                _emit(_event_record(args.correlation_id, event))
            if (
                state.polls == 1
                or result["events"]
                or result["gap_possible"]
                or state.polls % args.health_every_polls == 0
            ):
                _emit(_health_record(state, result, stale_after_s=args.stale_after))
            if args.state_file:
                save_state(args.state_file, state)
        except TimelineHttpError as exc:
            consecutive_errors += 1
            _emit(
                _health_record(
                    state, None, stale_after_s=args.stale_after, error=str(exc)
                )
            )
            if exc.status in {401, 403, 404, 422}:
                return 2
            if args.once:
                return 3 if exc.status == 503 else 1
        except (TimelineError, OSError, json.JSONDecodeError) as exc:
            consecutive_errors += 1
            _emit(
                _health_record(
                    state, None, stale_after_s=args.stale_after, error=str(exc)
                )
            )
            if args.once:
                return 1

        if args.once:
            return 0
        if consecutive_errors >= 5:
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())