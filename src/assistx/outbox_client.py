import os, json, sqlite3, time, threading, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Callable
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
    ):
        self.outbox_id = outbox_id
        self.event_id = event_id
        self.payload_json = payload_json
        self.attempt_count = attempt_count
        self.last_attempt_at = last_attempt_at
        self.status = status
        self.created_at = created_at or _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outbox_id": self.outbox_id,
            "event_id": self.event_id,
            "payload_json": self.payload_json,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at,
            "status": self.status,
            "created_at": self.created_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _basic_auth_header(user: str, password: str) -> str:
    import base64
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


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
    ):
        self.db_path = db_path
        self.api_url = api_url.rstrip("/")
        self.api_user = api_user
        self.api_pass = api_pass
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.retry_max_s = retry_max_s
        self._auth_header = _basic_auth_header(api_user, api_pass) if api_user or api_pass else None
        self._lock = threading.Lock()
        self._init_db()
        self._purge_expired()
        if auto_flush:
            self._flusher = threading.Thread(target=self._flush_loop, args=(flush_interval_s,), daemon=True)
            self._flusher.start()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
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
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)"
                )
                conn.commit()
            finally:
                conn.close()

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
                        "FROM outbox WHERE event_id=? AND status IN ('pending', 'failed', 'delivered') "
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
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE outbox SET status=?, attempt_count=?, last_attempt_at=? WHERE outbox_id=?",
                    (status, attempt_count, _now_iso(), outbox_id),
                )
                conn.commit()
            finally:
                conn.close()

    def flush(self, max_attempts: Optional[int] = None) -> int:
        max_attempts = max_attempts or self.max_retries
        max_retries = max_attempts
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT outbox_id, event_id, payload_json, attempt_count, last_attempt_at, status, created_at FROM outbox WHERE status IN ('pending', 'failed') ORDER BY created_at ASC LIMIT 50"
                ).fetchall()
            finally:
                conn.close()

        delivered = 0
        now = datetime.now(timezone.utc)
        for row in rows:
            entry = OutboxEntry(*row)
            if entry.attempt_count >= max_retries:
                self._update_status(entry.outbox_id, "dead", entry.attempt_count)
                continue
            # Exponential backoff: base * 2^attempt, capped at RETRY_MAX_S so a
            # failing destination gets at most one attempt per window instead of
            # one per flush tick.
            if entry.last_attempt_at and entry.attempt_count > 0:
                try:
                    last = datetime.fromisoformat(entry.last_attempt_at)
                    delay = min(RETRY_MAX_S, RETRY_BASE_S * (2 ** min(entry.attempt_count, 16)))
                    if (now - last).total_seconds() < delay:
                        continue
                except ValueError:
                    pass
            ok = self._deliver(entry)
            new_count = entry.attempt_count + 1
            if ok:
                self._update_status(entry.outbox_id, "delivered", new_count)
                delivered += 1
            else:
                if new_count >= max_retries:
                    self._update_status(entry.outbox_id, "failed", new_count)
                else:
                    self._update_status(entry.outbox_id, "pending", new_count)
        return delivered

    def _flush_loop(self, interval_s: int) -> None:
        while True:
            time.sleep(interval_s)
            try:
                self.flush()
            except Exception:
                pass

    def get_pending(self) -> List[OutboxEntry]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT outbox_id, event_id, payload_json, attempt_count, last_attempt_at, status, created_at FROM outbox WHERE status IN ('pending', 'failed') ORDER BY created_at ASC"
                ).fetchall()
            finally:
                conn.close()
        return [OutboxEntry(*row) for row in rows]

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM outbox GROUP BY status"
                ).fetchall()
            finally:
                conn.close()
        stats = {"total": 0}
        for status, count in rows:
            stats[status] = count
            stats["total"] += count
        return stats
