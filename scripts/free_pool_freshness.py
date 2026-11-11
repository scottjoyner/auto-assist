#!/usr/bin/env python3
"""Offline-only free-pool freshness report; NEVER an admission authority.

The historical free-pool snapshot is separate from passive provider-presence
and free-subagent session activity. Fresh session activity cannot renew it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import stat

MAX_BYTES = 2 * 1024 * 1024
MAX_AGE_SECONDS = 3600
STATE_ROOT = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/auto-assist/subagents"


def _safe_object(path: Path) -> tuple[dict, float]:
    """Bounded no-follow regular-file read; never return file data in report."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError("invalid_regular_file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("file_too_large")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("not_an_object")
        return obj, info.st_mtime
    finally:
        os.close(fd)


def _recent(timestamp: float, now: float, max_age: int) -> bool:
    return -30 <= now - timestamp <= max_age


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value or not (value.endswith("Z") or value.endswith("+00:00")):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.timestamp()


def review_state(root: Path = STATE_ROOT, *, now: datetime | None = None,
                 max_age: int = MAX_AGE_SECONDS) -> dict:
    """Local status only. A clean report still has no provider-side authority."""
    clock = (now or datetime.now(timezone.utc)).timestamp()
    reasons = []
    parsed = []
    for label, filename in (("projection", "free-pool-projection.json"),
                            ("refresh", "last-free-pool-refresh.json")):
        try:
            obj, modified = _safe_object(root / filename)
            parsed.append(obj)
            if not _recent(modified, clock, max_age):
                reasons.append(label + "_file_stale_or_future")
        except (OSError, UnicodeError, ValueError):
            parsed.append(None)
            reasons.append(label + "_unreadable_or_invalid")

    projection, refresh = parsed
    if projection is not None and (
        projection.get("schema") != "free-pool-projection/v1"
        or projection.get("read_only") is not True
        or projection.get("authoritative_routing_unchanged") is not True
    ):
        reasons.append("projection_contract_invalid")
    if refresh is not None:
        if (refresh.get("schema") != "free-pool-refresh-run/v1"
                or refresh.get("executed") is not True
                or refresh.get("paid_fallback") is not False):
            reasons.append("refresh_contract_invalid")
        ts = _timestamp(refresh.get("finished_at"))
        if ts is None or not _recent(ts, clock, max_age):
            reasons.append("refresh_completion_stale_or_invalid")

    reasons = sorted(set(reasons))
    return {
        "schema": "free-pool-local-freshness/v1",
        "read_only": True,
        "decision": "HOLD",
        "local_freshness": "stale_or_unknown" if reasons else "recent_untrusted_local_evidence",
        "blockers": reasons or ["account_and_upstream_authority_unverified"],
        "dispatch_allowed": False,
        "account_entitlement_proven": False,
        "provider_billing_verified": False,
        "shared_lease_enforced": False,
        "provider_requests": 0,
        "inference_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=STATE_ROOT)
    args = parser.parse_args()
    print(json.dumps(review_state(args.state_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
