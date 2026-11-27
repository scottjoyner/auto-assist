import os, json, sqlite3, time, threading, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Callable, Sequence
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DEFAULT_DB_PATH = os.path.expanduser("~/.assistx_outbox.db")
DEFAULT_API_URL = os.environ.get("ASSISTX_API_URL", "").strip()
DEFAULT_API_USER = os.environ.get("ASSISTX_AUTH_USER", "")
DEFAULT_API_PASS = os.environ.get("ASSISTX_AUTH_PASS", "")
MAX_RETRIES = int(os.environ.get("ASSISTX_OUTBOX_MAX_RETRIES", "10"))
RETRY_BASE_S = int(os.environ.get("ASSISTX_OUTBOX_RETRY_BASE_S", "5"))
RETRY_MAX_S = int(os.environ.get("ASSISTX_OUTBOX_RETRY_MAX_S", "300"))
# Guardrails: a destination that 422s forever must not accumulate an unbounded
# poison queue (2026-08-23 incident: ~300k pending entries retrying for hours).
TTL_DAYS = int(os.environ.get("ASSISTX_OUTBOX_TTL_DAYS", "7"))
MAX_PENDING = int(os.environ.get("ASSISTX_OUTBOX_MAX_PENDING", "100000"))
# Claim lease: how long a claimed row belongs to the flusher that claimed it
# before another flusher may recover it. Must comfortably exceed one delivery
# (15 s HTTP timeout) so a slow-but-alive flusher is never preempted, and stay
# short enough that a crashed flusher's rows come back quickly.
CLAIM_LEASE_S = int(os.environ.get("ASSISTX_OUTBOX_CLAIM_LEASE_S", "90"))
# Rows one flush() call may claim. Bounded so a huge backlog cannot hold a
# single flush for minutes while other flushers idle.
FLUSH_BATCH = int(os.environ.get("ASSISTX_OUTBOX_FLUSH_BATCH", "50"))
# Readiness rule: a deliverable row older than this means the queue is not
# draining. Default 24 h.
STALE_READINESS_S = int(os.environ.get("ASSISTX_OUTBOX_STALE_S", "86400"))

STATUSES = ("pending", "failed", "claimed", "delivered", "dead")


class OutboxEntry:
    def __init__(
        self,
        outbox_id: str,
        event_id: str,
        payload_json: str,
        attempt_count: int = 0,
        last_attempt_at: Optional[str] = None,
        status: str = "pending",
        created_at: Optional[str] = None,
        claim_owner: Optional[str] = None,
        claimed_at: Optional[str] = None,
    ):
        self.outbox_id = outbox_id
        self.event_id = event_id
        self.payload_json = payload_json
        self.attempt_count = attempt_count
        self.last_attempt_at = last_attempt_at
        self.status = status
        self.created_at = created_at or _now_iso()
        # Set only while this process holds the row (status == 'claimed').
        self.claim_owner = claim_owner
        self.claimed_at = claimed_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outbox_id": self.outbox_id,
            "event_id": self.event_id,
            "payload_json": self.payload_json,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at,
            "status": self.status,
            "created_at": self.created_at,
            "claim_owner": self.claim_owner,
            "claimed_at": self.claimed_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _basic_auth_header(user: str, password: str) -> str:
    import base64
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_queue_health(
    db_path: str = DEFAULT_DB_PATH,
    *,
    stale_after_s: Optional[int] = None,
    now: Optional[datetime] = None,
    read_only: bool = True,
) -> Dict[str, Any]:
    """Report outbox depth and staleness without writing to the database.

    Safe to call from a health probe: it opens the DB read-only (``mode=ro``),
    so it never creates, migrates or purges a live queue. A missing database
    reads as ``absent`` and healthy — no queue is not a broken queue.

    ``ok`` is False when the oldest deliverable row is older than
    ``stale_after_s`` (default 24 h) or when an existing queue cannot be read
    at all: depth alone is not a fault, but unverifiable depth is not green.
    """
    now = now or datetime.now(timezone.utc)
    threshold = STALE_READINESS_S if stale_after_s is None else int(stale_after_s)
    path = os.path.expanduser(db_path or DEFAULT_DB_PATH)
    health: Dict[str, Any] = {
        "db_path": path,
        "status": "absent",
        "ok": True,
        "reason": "outbox database not present",
        "stale_after_s": threshold,
        "pending": 0,
        "failed": 0,
        "claimed": 0,
        "delivered": 0,
        "dead": 0,
        "total": 0,
        "oldest_pending_at": None,
        "oldest_pending_age_s": None,
    }
    if not path or not os.path.exists(path):
        return health
    try:
        if read_only:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        else:
            conn = sqlite3.connect(path, timeout=5)
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM outbox GROUP BY status"
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM outbox "
                "WHERE status IN ('pending', 'failed', 'claimed')"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        health.update(
            {
                "status": "error",
                # Fail closed: a corrupt/locked queue cannot prove it is
                # draining. The r39 runtime guard only covers a probe that
                # raises; sqlite3.Error is swallowed here, so the old True
                # default kept readiness green with zero queue evidence.
                "ok": False,
                "reason": f"outbox unreadable: {str(exc)[:200]}",
            }
        )
        return health

    for status, count in rows:
        health[status] = int(count)
        health["total"] += int(count)
    oldest_at = oldest[0] if oldest else None
    health["oldest_pending_at"] = oldest_at
    oldest_dt = _parse_iso(oldest_at)
    if oldest_dt is not None:
        age = max(0.0, (now - oldest_dt).total_seconds())
        health["oldest_pending_age_s"] = round(age, 3)
        if age > threshold:
            health["status"] = "stale"
            health["ok"] = False
            health["reason"] = (
                f"oldest pending event is {age / 3600:.1f}h old "
                f"(threshold {threshold / 3600:.1f}h)"
            )
            return health
    health["status"] = "ok"
    health["ok"] = True
    health.pop("reason", None)
    return health


class OutboxClient:
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        api_url: str = DEFAULT_API_URL,
        api_user: str = DEFAULT_API_USER,
        api_pass: str = DEFAULT_API_PASS,
        max_retries: int = MAX_RETRIES,
        retry_base_s: int = RETRY_BASE_S,
        retry_max_s: int = RETRY_MAX_S,
        auto_flush: bool = True,
        flush_interval_s: int = 30,
        claim_lease_s: int = CLAIM_LEASE_S,
        flush_batch: int = FLUSH_BATCH,
    ):
        self.db_path = db_path
        self.api_url = api_url.rstrip("/")
        self.api_user = api_user
        self.api_pass = api_pass
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.retry_max_s = retry_max_s
        self.claim_lease_s = claim_lease_s
        self.flush_batch = flush_batch
        self._auth_header = _basic_auth_header(api_user, api_pass) if api_user or api_pass else None
        self._lock = threading.Lock()
        # Counts flush() calls skipped because no destination is configured, so
        # a misconfigured producer is observable instead of silently draining
        # retry budgets.
        self.skipped_unconfigured = 0
        self._init_db()
        self._purge_expired()
        if auto_flush:
            self._flusher = threading.Thread(target=self._flush_loop, args=(flush_interval_s,), daemon=True)
            self._flusher.start()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outbox (
                        outbox_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        attempt_count INTEGER DEFAULT 0,
                        last_attempt_at TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT NOT NULL,
                        claim_owner TEXT,
                        claimed_at TEXT
                    )
                    """
                )
                self._migrate_schema(conn)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """Add the claim columns to pre-existing databases.

        Additive only: ALTER TABLE ADD COLUMN, each guarded by a
        PRAGMA table_info probe, so a queue created before the claim protocol
        keeps its rows (and its ``pending`` status) and simply gains the new
        nullable columns. Terminal states are unchanged; ``claimed`` is the one
        new status and only exists between claim and release.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(outbox)").fetchall()}
        if "claim_owner" not in columns:
            conn.execute("ALTER TABLE outbox ADD COLUMN claim_owner TEXT")
        if "claimed_at" not in columns:
            conn.execute("ALTER TABLE outbox ADD COLUMN claimed_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_claim ON outbox(status, claimed_at)"
        )

    def _purge_expired(self) -> None:
        """Delete terminal rows older than the TTL so the db cannot grow forever."""
        if TTL_DAYS <= 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)).isoformat()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "DELETE FROM outbox WHERE status IN ('delivered', 'dead') AND created_at < ?",
                    (cutoff,),
                )
                conn.commit()
            finally:
                conn.close()

    def _enforce_pending_cap(self, conn: sqlite3.Connection) -> int:
        """Demote oldest pending rows to 'dead' when the queue exceeds MAX_PENDING.

        Stale notifications are worthless next to recent state; dropping from
        the front keeps the newest events deliverable and bounds retry load.
        Returns the number of demoted rows.
        """
        if MAX_PENDING <= 0:
            return 0
        depth = conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE status IN ('pending', 'failed')"
        ).fetchone()[0]
        excess = depth - MAX_PENDING
        if excess <= 0:
            return 0
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT outbox_id FROM outbox WHERE status IN ('pending', 'failed') "
                "ORDER BY created_at ASC LIMIT ?",
                (excess,),
            ).fetchall()
        ]
        conn.executemany(
            "UPDATE outbox SET status='dead' WHERE outbox_id=?",
            [(i,) for i in ids],
        )
        return len(ids)

    def enqueue(self, event: Dict[str, Any]) -> OutboxEntry:
        event_id = str(event.get("event_id", ""))
        outbox_id = str(uuid.uuid4())
        entry = OutboxEntry(
            outbox_id=outbox_id,
            event_id=event_id,
            payload_json=json.dumps(event),
            status="pending",
        )
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                if event_id:
                    row = conn.execute(
                        "SELECT outbox_id, event_id, payload_json, attempt_count, last_attempt_at, status, created_at "
                        "FROM outbox WHERE event_id=? AND status IN ('pending', 'failed', 'claimed', 'delivered') "
                        "ORDER BY created_at ASC LIMIT 1",
                        (event_id,),
                    ).fetchone()
                    if row:
                        return OutboxEntry(*row)
                conn.execute(
                    "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, last_attempt_at, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entry.outbox_id, entry.event_id, entry.payload_json, entry.attempt_count, entry.last_attempt_at, entry.status, entry.created_at),
                )
                self._enforce_pending_cap(conn)
                conn.commit()
            finally:
                conn.close()
        return entry

    def _deliver(self, entry: OutboxEntry) -> bool:
        if not self.api_url:
            return False
        url = f"{self.api_url}/api/events"
        data = entry.payload_json.encode("utf-8")
        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        try:
            with urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
                result = json.loads(body)
                accepted = result.get("accepted", False)
                if accepted:
                    return True
                return False
        except (URLError, HTTPError, OSError, json.JSONDecodeError):
            return False

    def _update_status(self, outbox_id: str, status: str, attempt_count: int) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            try:
                conn.execute(
                    "UPDATE outbox SET status=?, attempt_count=?, last_attempt_at=? WHERE outbox_id=?",
                    (status, attempt_count, _now_iso(), outbox_id),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Claim protocol
    #
    # Two flushers used to read the same pending rows under a lock, release
    # the lock, then deliver outside it: both delivered the same event. The
    # claim step closes that window. A row must be claimed (status='claimed',
    # claim_owner=<per-claim token>, claimed_at=<now>) before it can be
    # delivered, and the claim is taken inside a single BEGIN IMMEDIATE
    # transaction that both selects and marks. A claimer that never releases
    # (crash, SIGKILL) loses the row after claim_lease_s and any flusher may
    # recover it -- delivery is at-least-once, but it is no longer
    # *concurrently* at-least-once.
    # ------------------------------------------------------------------

    def _claim_next(
        self,
        *,
        exclude: Optional[Sequence[str]] = None,
        lease_s: Optional[int] = None,
    ) -> Optional[OutboxEntry]:
        """Atomically claim the oldest deliverable row, or return None.

        Deliverable = pending, failed, or claimed by someone whose lease has
        expired. The returned entry carries the token in ``claim_owner``;
        every later write must present it so a recovered row cannot be
        clobbered by the flusher that lost it.
        """
        lease = self.claim_lease_s if lease_s is None else int(lease_s)
        now = datetime.now(timezone.utc)
        stale_cutoff = (now - timedelta(seconds=max(1, lease))).isoformat()
        claimed_at = now.isoformat()
        token = uuid.uuid4().hex
        exclude = list(exclude or ())

        where = [
            "(status IN ('pending', 'failed')"
            " OR (status = 'claimed' AND (claimed_at IS NULL OR claimed_at < ?)))"
        ]
        params: List[Any] = [stale_cutoff]
        if exclude:
            where.append(f"outbox_id NOT IN ({','.join('?' * len(exclude))})")
            params.extend(exclude)

        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.isolation_level = None  # explicit transaction control
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT outbox_id, event_id, payload_json, attempt_count, "
                    "last_attempt_at, status, created_at FROM outbox "
                    f"WHERE {' AND '.join(where)} ORDER BY created_at ASC LIMIT 1",
                    params,
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                # Guarded update: the WHERE clause re-asserts deliverability so
                # this can only ever claim a row nobody else holds a live claim
                # on, even if run outside BEGIN IMMEDIATE.
                updated = conn.execute(
                    "UPDATE outbox SET status='claimed', claim_owner=?, claimed_at=? "
                    "WHERE outbox_id=? AND (status IN ('pending', 'failed')"
                    " OR (status = 'claimed' AND (claimed_at IS NULL OR claimed_at < ?)))",
                    (token, claimed_at, row[0], stale_cutoff),
                ).rowcount
                if not updated:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

        entry = OutboxEntry(*row)
        entry.status = "claimed"
        entry.claim_owner = token
        entry.claimed_at = claimed_at
        return entry

    def _release(
        self,
        entry: OutboxEntry,
        status: str,
        attempt_count: int,
        *,
        touch: bool = True,
    ) -> bool:
        """Hand a claimed row back, only if we still hold the claim.

        Returns False when the lease was recovered by another flusher, in which
        case this process must not overwrite the row.
        """
        sets = ["status=?", "attempt_count=?", "claim_owner=NULL", "claimed_at=NULL"]
        params: List[Any] = [status, attempt_count]
        if touch:
            sets.insert(2, "last_attempt_at=?")
            params.append(_now_iso())
        params.extend([entry.outbox_id, entry.claim_owner])
        sql = (
            f"UPDATE outbox SET {', '.join(sets)} WHERE outbox_id=? "
            "AND (claim_owner IS NULL OR claim_owner=?)"
        )
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            try:
                changed = conn.execute(sql, params).rowcount
                conn.commit()
            finally:
                conn.close()
        return bool(changed)

    def _backoff_remaining_s(self, entry: OutboxEntry, now: datetime) -> float:
        """Seconds still owed on the retry backoff, 0.0 when the row is due."""
        last = _parse_iso(entry.last_attempt_at)
        if last is None or entry.attempt_count <= 0:
            return 0.0
        delay = min(self.retry_max_s, self.retry_base_s * (2 ** min(entry.attempt_count, 16)))
        return max(0.0, delay - (now - last).total_seconds())

    def flush(self, max_attempts: Optional[int] = None) -> int:
        max_attempts = max_attempts or self.max_retries
        max_retries = max_attempts
        # A missing destination is a configuration fault, not a delivery
        # failure. Burning an attempt here would silently kill every queued
        # event (2026-11 incident: 78 rows wedged at attempt_count=9 on a
        # process started without ASSISTX_API_URL). Leave the rows untouched
        # and surface the condition to the caller as "nothing attempted".
        if not self.api_url:
            self.skipped_unconfigured += 1
            return 0

        delivered = 0
        seen: List[str] = []
        while len(seen) < self.flush_batch:
            # Claim first, deliver second: the row belongs to this flusher for
            # claim_lease_s, so a second flusher cannot pick it up until either
            # we release it or our lease expires.
            entry = self._claim_next(exclude=seen)
            if entry is None:
                break
            seen.append(entry.outbox_id)
            now = datetime.now(timezone.utc)
            if entry.attempt_count >= max_retries:
                # Exhaustion is terminal. 'dead' is the only status the purge
                # path recognises, so landing in 'failed' here left rows that
                # were neither retried to completion nor ever collected, and a
                # later flush(max_attempts=<higher>) could resurrect them.
                self._release(entry, "dead", entry.attempt_count)
                continue
            # Exponential backoff: base * 2^attempt, capped at RETRY_MAX_S so a
            # failing destination gets at most one attempt per window instead
            # of one per flush tick. Releasing without touching
            # last_attempt_at keeps the window from sliding forward.
            if self._backoff_remaining_s(entry, now) > 0:
                self._release(entry, "pending", entry.attempt_count, touch=False)
                continue
            ok = self._deliver(entry)
            new_count = entry.attempt_count + 1
            if ok:
                if self._release(entry, "delivered", new_count):
                    delivered += 1
            elif new_count >= max_retries:
                self._release(entry, "dead", new_count)
            else:
                self._release(entry, "pending", new_count)
        return delivered

    def _flush_loop(self, interval_s: int) -> None:
        while True:
            time.sleep(interval_s)
            try:
                self.flush()
            except Exception:
                pass

    def get_pending(self) -> List[OutboxEntry]:
        """Rows still owed to the destination (pending or failed).

        Claimed rows are deliberately excluded: they belong to an in-flight
        flusher and are not selectable work. Use ``get_stats``/``queue_health``
        to see them.
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            try:
                rows = conn.execute(
                    "SELECT outbox_id, event_id, payload_json, attempt_count, last_attempt_at, status, created_at FROM outbox WHERE status IN ('pending', 'failed') ORDER BY created_at ASC"
                ).fetchall()
            finally:
                conn.close()
        return [OutboxEntry(*row) for row in rows]

    def queue_health(
        self,
        *,
        stale_after_s: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Depth + staleness snapshot of this client's outbox."""
        return read_queue_health(
            self.db_path, stale_after_s=stale_after_s, now=now, read_only=False
        )

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM outbox GROUP BY status"
                ).fetchall()
            finally:
                conn.close()
        stats = {"total": 0}
        # Report every known status, including the ones at zero. Callers index
        # stats["dead"] / stats["pending"] directly, and a status that has
        # never occurred must read as 0 rather than raise KeyError.
        for known in STATUSES:
            stats[known] = 0
        for status, count in rows:
            stats[status] = count
            stats["total"] += count
        return stats
