"""Sandbox-only source-claim signature and replay admission research.

NOT a production trace attestor. RegisteredSource trust is supplied independently
by the caller; this module never reads environment secrets, private traces,
graph data, NAS or production services. HMAC proves only key possession.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

SCHEMA = "assistx.trace-source-claim.v1"
DOMAIN = b"ASSISTX-TRACE-SOURCE-CLAIM-V1\n"
CLAIM_FIELDS = frozenset({
    "schema", "key_id", "node_id", "agent_id", "source_service",
    "generation", "correlation_id", "task_id", "event_id",
    "event_sha256", "issued_at_ms", "expires_at_ms", "nonce",
})
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
HEX_NONCE = re.compile(r"^[0-9a-f]{32,64}$")
MAX_SERIALIZED = 4096
MAX_DURATION_MS = 120_000
MAX_FUTURE_SKEW_MS = 10_000


@dataclass(frozen=True)
class RegisteredSource:
    """Operator-provisioned outside the event/graph path; never public API data."""
    key_id: str
    node_id: str
    agent_id: str
    source_service: str
    generation: str
    secret: bytes = field(repr=False)
    active: bool = True
    not_before_ms: int = 0
    not_after_ms: int = 2**63 - 1


@dataclass(frozen=True)
class ExpectedEvent:
    """Expected values must come from an *independently trusted* input."""
    correlation_id: str
    task_id: str
    event_id: str
    event_sha256: str


def _deny(reason: str, integrity: bool = False) -> dict[str, Any]:
    return {
        "schema": "assistx.trace-attestation-decision.v1",
        "status": reason,
        "claim_integrity_valid": integrity,
        "local_replay_accepted": False,
        "execution_attested": False,
        "source_hardware_attested": False,
        "custody_independently_witnessed": False,
        "production_authorized": False,
    }


def _distinct_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("DUPLICATE_JSON_KEY")
        obj[key] = value
    return obj


def _strict_json(raw: str | bytes) -> dict[str, Any]:
    if not isinstance(raw, (str, bytes)) or len(raw) > MAX_SERIALIZED:
        raise ValueError("INVALID_ENVELOPE")
    try:
        obj = json.loads(raw, object_pairs_hook=_distinct_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("INVALID_CONSTANT")))
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("INVALID_ENVELOPE_JSON") from None
    if not isinstance(obj, dict) or set(obj) != {"claim", "signature"}:
        raise ValueError("INVALID_ENVELOPE_FIELDS")
    return obj


def _valid_text(value: Any, length: int = 128) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= length
            and value == value.strip()
            and all(32 <= ord(c) <= 126 for c in value))


def _canonical(claim: dict[str, Any]) -> bytes:
    return DOMAIN + json.dumps(
        claim, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False
    ).encode("utf-8")


def _shape(claim: Any, signature: Any) -> bool:
    if not isinstance(claim, dict) or set(claim) != CLAIM_FIELDS:
        return False
    if claim.get("schema") != SCHEMA:
        return False
    if not isinstance(signature, str) or not HEX_64.fullmatch(signature):
        return False
    for name in ("key_id", "node_id", "agent_id", "source_service",
                 "generation", "correlation_id", "task_id", "event_id"):
        if not _valid_text(claim[name], 128):
            return False
    if not HEX_NONCE.fullmatch(str(claim["nonce"])):
        return False
    if not isinstance(claim["nonce"], str):
        return False
    if not isinstance(claim["event_sha256"], str) or not HEX_64.fullmatch(claim["event_sha256"]):
        return False
    if type(claim["issued_at_ms"]) is not int or type(claim["expires_at_ms"]) is not int:
        return False
    try:
        if str(UUID(claim["correlation_id"])) != claim["correlation_id"]:
            return False
    except (ValueError, AttributeError):
        return False
    return True


class SandboxReplayLedger:
    """Test-only SQLite unique-claim store, not external immutable witnessing.

    Caller supplies an explicit disposable directory and owns its lifecycle.
    There is no production default location or background process.
    """
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.parent.is_dir() or self.path.is_symlink():
            raise ValueError("UNSAFE_LEDGER_PARENT")
        with sqlite3.connect(str(self.path), timeout=5) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS admissions (
                  key_id TEXT NOT NULL,
                  nonce TEXT NOT NULL,
                  event_id TEXT NOT NULL,
                  event_sha256 TEXT NOT NULL,
                  admitted_at_ms INTEGER NOT NULL,
                  PRIMARY KEY (key_id, nonce),
                  UNIQUE (key_id, event_id)
                )
            """)
            db.commit()
        st = os.lstat(self.path)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("UNSAFE_LEDGER_FILE")
        self._identity = (st.st_dev, st.st_ino)

    def _check_identity(self) -> None:
        # A moved/replaced ledger can erase the replay history. Fail closed
        # within a running verifier; independent anchoring is still required
        # to detect rollback before a *new* verifier initializes.
        st = os.lstat(self.path)
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != self._identity:
            raise sqlite3.OperationalError("LEDGER_FILE_REPLACED")

    def accept(self, claim: dict[str, Any], now_ms: int) -> bool:
        try:
            self._check_identity()
            with sqlite3.connect(str(self.path), timeout=5, isolation_level=None) as db:
                self._check_identity()
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO admissions VALUES (?, ?, ?, ?, ?)",
                    (claim["key_id"], claim["nonce"], claim["event_id"],
                     claim["event_sha256"], now_ms)
                )
                db.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            return False


def verify_source_claim(
    raw: str | bytes,
    registered: Mapping[str, RegisteredSource],
    expected: ExpectedEvent,
    now_ms: int,
    replay_ledger: SandboxReplayLedger | None = None,
) -> dict[str, Any]:
    """Read-only signature validation plus OPTIONAL synthetic replay admission.

    Positive state is *never* execution attestation. Neither registry nor
    expected event may originate solely from the claim being verified.
    """
    if type(now_ms) is not int or now_ms < 0 or not isinstance(expected, ExpectedEvent):
        return _deny("INVALID_VERIFIER_INPUT")
    try:
        envelope = _strict_json(raw)
        claim, signature = envelope["claim"], envelope["signature"]
        if not _shape(claim, signature):
            return _deny("INVALID_CLAIM_SCHEMA")
        key = registered.get(claim["key_id"])
        if not isinstance(key, RegisteredSource) or not key.active:
            return _deny("KEY_UNAVAILABLE_OR_REVOKED")
        if len(key.secret) < 32 or not isinstance(key.secret, bytes):
            return _deny("UNSAFE_TEST_KEY")
        if (
            claim["key_id"] != key.key_id
            or claim["node_id"] != key.node_id
            or claim["agent_id"] != key.agent_id
            or claim["source_service"] != key.source_service
            or claim["generation"] != key.generation
        ):
            return _deny("REGISTERED_IDENTITY_MISMATCH")
        if (
            claim["correlation_id"] != expected.correlation_id
            or claim["task_id"] != expected.task_id
            or claim["event_id"] != expected.event_id
            or claim["event_sha256"] != expected.event_sha256
        ):
            return _deny("EVENT_BINDING_MISMATCH")
        issued, expires = claim["issued_at_ms"], claim["expires_at_ms"]
        if (
            issued < 0 or expires <= issued
            or expires - issued > MAX_DURATION_MS
            or issued > now_ms + MAX_FUTURE_SKEW_MS
            or now_ms > expires
            or issued < key.not_before_ms
            or expires > key.not_after_ms
        ):
            return _deny("CLAIM_OUTSIDE_TIME_WINDOW")
        correct = hmac.new(key.secret, _canonical(claim), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, correct):
            return _deny("INVALID_CLAIM_SIGNATURE")
        if replay_ledger is None:
            return _deny("REPLAY_LEDGER_REQUIRED", integrity=True)
        if not replay_ledger.accept(claim, now_ms):
            return _deny("DUPLICATE_NONCE_OR_EVENT", integrity=True)
        response = _deny("LOCAL_CLAIM_INTEGRITY_ACCEPTED", integrity=True)
        response["local_replay_accepted"] = True
        return response
    except (ValueError, TypeError, KeyError):
        return _deny("INVALID_CLAIM")
    except sqlite3.Error:
        return _deny("REPLAY_LEDGER_UNAVAILABLE")
