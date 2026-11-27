"""Fleet coordination guardrail.

A tiny advisory lock service so agents and operators do not collide on shared
inference infrastructure (LM Studio model loads, router restarts, benchmark
runs). State lives in one JSON file on shared storage so every tailnet node can
consult it with or without API access:

    {"exclusive_ops": {"benchmark": {"owner": "...", "expires_at": "...", ...}}}

Convention: before restarting LM Studio / the router / changing model loads,
POST a claim for the op you are about to perform and check what is already
claimed. Claims expire (ttl) so stale locks cannot wedge the fleet.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

DEFAULT_COORDINATION_FILE = os.path.expanduser("~/.hermes/FLEET-COORDINATION.json")
DEFAULT_TTL_MINUTES = 120
MAX_TTL_MINUTES = 24 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class CoordinationStateError(RuntimeError):
    """An existing coordination state file cannot be read or is malformed."""


class CoordinationStore:
    """JSON-file backed advisory lock store. Safe for concurrent readers."""

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("FLEET_COORDINATION_FILE", DEFAULT_COORDINATION_FILE))
        self._lock = threading.Lock()

    def _read_raw(self, *, fail_on_unreadable: bool = False) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            # A state file that does not exist yet is a genuinely empty store.
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            if fail_on_unreadable:
                raise CoordinationStateError(
                    f"coordination state unreadable: {exc}"
                ) from exc
            return {}
        if not isinstance(data, dict):
            if fail_on_unreadable:
                raise CoordinationStateError(
                    "coordination state is not a JSON object"
                )
            return {}
        return data

    def _write_raw(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def _prune_expired(self, data: dict) -> dict:
        ops = data.get("exclusive_ops")
        if not isinstance(ops, dict):
            return data
        now = _now()
        active = {
            op: entry
            for op, entry in ops.items()
            if (_parse_ts((entry or {}).get("expires_at")) or now) > now
        }
        data["exclusive_ops"] = active
        return data

    def snapshot(self) -> dict:
        with self._lock:
            data = self._prune_expired(self._read_raw())
            data.setdefault("exclusive_ops", {})
            data["updated_at"] = (
                _now().isoformat()
            )
            return data

    def claim(
        self,
        op: str,
        owner: str,
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
        note: str = "",
    ) -> dict:
        if not op or not owner:
            raise HTTPException(status_code=422, detail="op and owner are required")
        ttl_minutes = max(1, min(int(ttl_minutes or DEFAULT_TTL_MINUTES), MAX_TTL_MINUTES))
        now = _now()
        expires = now + timedelta(minutes=ttl_minutes)
        with self._lock:
            # Never grant an exclusive claim when an existing state file cannot
            # be parsed: treating it as empty would let a second owner claim an
            # op that may already be locked (and overwrite the only record of
            # the first owner). Read-only snapshots stay tolerant.
            try:
                data = self._prune_expired(
                    self._read_raw(fail_on_unreadable=True)
                )
                if "exclusive_ops" in data and not isinstance(
                    data["exclusive_ops"], dict
                ):
                    raise CoordinationStateError(
                        "exclusive_ops is not a JSON object"
                    )
            except CoordinationStateError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "coordination state unreadable; refusing claim",
                        "op": op,
                        "error": str(exc),
                    },
                ) from exc
            ops = data.setdefault("exclusive_ops", {})
            existing = ops.get(op)
            if isinstance(existing, dict) and existing.get("owner") != owner:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"op '{op}' is claimed by another owner",
                        "claim": existing,
                    },
                )
            ops[op] = {
                "owner": owner,
                "started_at": (existing or {}).get("started_at") or now.isoformat(),
                "expires_at": expires.isoformat(),
                "note": note or (existing or {}).get("note", ""),
            }
            data["updated_at"] = now.isoformat()
            self._write_raw(data)
            return {"claimed": True, "op": op, "claim": ops[op]}

    def release(self, op: str, owner: str) -> dict:
        with self._lock:
            data = self._prune_expired(self._read_raw())
            ops = data.get("exclusive_ops") or {}
            existing = ops.get(op)
            if not isinstance(existing, dict):
                return {"released": False, "reason": "no active claim"}
            if existing.get("owner") != owner:
                raise HTTPException(
                    status_code=403,
                    detail=f"op '{op}' is owned by '{existing.get('owner')}'",
                )
            del ops[op]
            data["updated_at"] = _now().isoformat()
            self._write_raw(data)
            return {"released": True, "op": op}


def build_coordination_router(auth_dependency=None, path: str | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/coordination", tags=["coordination"])
    store = CoordinationStore(path)

    def _guard(user=auth_dependency):
        return user

    @router.get("")
    def status(user=auth_dependency):
        _guard(user)
        return store.snapshot()

    @router.post("/claim")
    def claim(payload: dict, user=auth_dependency):
        _guard(user)
        return store.claim(
            str(payload.get("op") or ""),
            str(payload.get("owner") or ""),
            ttl_minutes=payload.get("ttl_minutes") or DEFAULT_TTL_MINUTES,
            note=str(payload.get("note") or ""),
        )

    @router.post("/release")
    def release(payload: dict, user=auth_dependency):
        _guard(user)
        return store.release(str(payload.get("op") or ""), str(payload.get("owner") or ""))

    return router
