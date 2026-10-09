"""Research-only staging wrapper for cooperative trace-index read cancellation.

NOT imported by FastAPI. No I/O at import. A caller must *independently*
authenticate its principal and supply an isolated Redis client, the receiver
owned key, and a read-only, cancellation-aware query callback.

A synchronous Neo4j 6.2 Session/Transaction has no public cancel() method.
This wrapper therefore NEVER claims it can forcibly interrupt arbitrary
blocking queries: a cancellation hook is mandatory, and revocation prevents
returning results. Physical cancellation remains an acceptance gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Any, Callable, TypeVar

from .trace_index_fenced_research import (
    AdmissionUnavailable,
    Policy,
    acquire,
    renew,
    release,
)

T = TypeVar("T")


class FencedReadUnavailable(RuntimeError):
    """Fail-closed when the lease or its release cannot be established."""


@dataclass(frozen=True)
class FencedReadDenied(RuntimeError):
    """True, admitted-identity rate or in-flight exhaustion (not Redis outage)."""
    reason: str
    retry_after_seconds: int

    def __str__(self) -> str:
        return "synthetic read quota exhausted"


@dataclass(frozen=True)
class StagingParameters:
    heartbeat_seconds: float = 0.0   # 0 chooses <=1/3 of lease duration
    watchdog_join_seconds: float = 2.0


def run_staging_fenced_read(
    *,
    redis_client: Any,
    principal: str,
    receiver_key: str,
    query: Callable[[Event], T],
    request_cancel: Callable[[], None],
    policy: Policy = Policy(),
    parameters: StagingParameters = StagingParameters(),
) -> T:
    """Execute one *cooperative* read under exact-owned rate/concurrency lease.

    Read callback must poll `cancelled`, and request_cancel must interrupt
    transport work where actually supported. There is NO query thread spawned;
    if a synchronous query blocks forever, this caller can also block forever.
    The watchdog cannot forcibly stop it. Do not use with live Neo4j yet.

    On all normal/exceptional exits, stop watchdog and attempt exact-nonce
    release. If the lease cannot be confirmed at release time, never return
    even a successful query response. Redis errors never authorize output.
    """
    if not callable(query) or not callable(request_cancel):
        raise FencedReadUnavailable("cancellation-aware query contract missing")
    if not isinstance(parameters, StagingParameters):
        raise FencedReadUnavailable("invalid staging timing policy")
    try:
        decision = acquire(redis_client, principal, receiver_key, policy)
    except AdmissionUnavailable as exc:
        raise FencedReadUnavailable("shared read admission unavailable") from exc
    if not decision.allowed:
        raise FencedReadDenied(decision.reason, decision.retry_after_seconds)
    lease = decision.lease
    if lease is None:
        raise FencedReadUnavailable("admitted request missing owned lease")
    interval = parameters.heartbeat_seconds or min(3.0, lease.lease_seconds / 3)
    if not 0 < interval < lease.lease_seconds / 2:
        # Always release the already acquired slot on invalid caller timing.
        try:
            release(redis_client, lease)
        except AdmissionUnavailable:
            pass
        raise FencedReadUnavailable("invalid heartbeat interval")
    if not 0 < parameters.watchdog_join_seconds <= 10:
        try:
            release(redis_client, lease)
        except AdmissionUnavailable:
            pass
        raise FencedReadUnavailable("invalid watchdog join limit")

    stopped = Event()
    cancelled = Event()
    watchdog_finished = Event()
    failures: list[str] = []

    def trigger_cancel() -> None:
        cancelled.set()
        try:
            request_cancel()
        except Exception:
            # A failing transport cancellation is NOT successful authority.
            failures.append("transport cancellation unconfirmed")

    def heartbeat() -> None:
        try:
            while not stopped.wait(interval):
                try:
                    if not renew(redis_client, lease):
                        failures.append("renewal rejected")
                        trigger_cancel()
                        return
                except Exception:
                    failures.append("renewal unavailable")
                    trigger_cancel()
                    return
        finally:
            watchdog_finished.set()

    thread = Thread(target=heartbeat, name="synthetic-trace-fence-renewal", daemon=True)
    thread.start()
    result: T | None = None
    query_error: Exception | None = None
    try:
        result = query(cancelled)
    except Exception as exc:
        query_error = exc
    finally:
        stopped.set()
        thread.join(timeout=parameters.watchdog_join_seconds)
        if not watchdog_finished.is_set() or thread.is_alive():
            failures.append("renewal worker did not stop")
            trigger_cancel()
        try:
            confirmed_release = release(redis_client, lease)
            if not confirmed_release:
                failures.append("owned lease absent on release")
                trigger_cancel()
        except Exception:
            failures.append("lease release unavailable")
            trigger_cancel()

    if cancelled.is_set() or failures:
        raise FencedReadUnavailable("read authority lost or cleanup unconfirmed")
    if query_error is not None:
        raise query_error
    return result  # type: ignore[return-value]


def staging_http_disposition(exc: Exception) -> tuple[int, dict[str, str]]:
    """Pure mapping only; NOT registered in deployed FastAPI middleware."""
    if isinstance(exc, FencedReadDenied):
        return 429, {"Retry-After": str(exc.retry_after_seconds)}
    if isinstance(exc, FencedReadUnavailable):
        return 503, {"Retry-After": "5"}
    raise TypeError("unrecognized staging admission error")
