"""SYNTHETIC SQLite nonce replay fixture. No production writer or authority.

Only disposable, test-owned sqlite files may be bootstrapped. A real K2 witness
requires separately administered issuance, custody, authenticated CAS and
operator approval. This fixture proves atomic replay behavior, not provenance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

NONCE = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_ISSUANCE_LIFETIME_MS = 300_000
MAX_RECEIPT_BYTES = 64 * 1024


class ChallengeHold(ValueError):
    """Fail-closed synthetic nonce ledger refusal."""


def _check_nonce(value: str) -> None:
    if not isinstance(value, str) or not NONCE.fullmatch(value):
        raise ChallengeHold("synthetic_challenge_invalid")


class SyntheticChallengeLedger:
    """A durability/concurrency test fixture, deliberately NOT an adapter.

    The class is not imported by the production K2 verifier. Merely having
    a local SQLite database cannot establish independently custodied nonce
    issuance, a trusted signed CAS receipt, or external rollback protection.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def bootstrap_new(cls, path: Path) -> SyntheticChallengeLedger:
        target = Path(path)
        if target.exists() or target.is_symlink() or not target.parent.is_dir():
            raise ChallengeHold("synthetic_ledger_not_new")
        # Never open or replace an existing user or service DB.
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            raise ChallengeHold("synthetic_ledger_creation_refused") from exc
        os.close(fd)
        self = cls(target)
        with self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE challenges (
                    nonce TEXT PRIMARY KEY CHECK(length(nonce) = 64),
                    issued_ms INTEGER NOT NULL,
                    expires_ms INTEGER NOT NULL,
                    spent_ms INTEGER,
                    spent_receipt_sha256 TEXT,
                    sequence INTEGER UNIQUE,
                    CHECK(expires_ms > issued_ms),
                    CHECK((spent_ms IS NULL AND spent_receipt_sha256 IS NULL
                            AND sequence IS NULL) OR
                          (spent_ms IS NOT NULL AND spent_receipt_sha256 IS NOT NULL
                            AND sequence IS NOT NULL))
                )
            """)
            db.execute("""
                CREATE TABLE highwater(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    sequence INTEGER NOT NULL CHECK(sequence >= 0)
                )
            """)
            db.execute("INSERT INTO highwater(id,sequence) VALUES (1,0)")
        return self

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink() or not self.path.is_file():
            raise ChallengeHold("synthetic_ledger_missing_or_symlink")
        try:
            # mode=rw refuses creating a missing/replaced database.
            db = sqlite3.connect(
                self.path.absolute().as_uri() + "?mode=rw",
                uri=True, timeout=3.0, isolation_level=None,
            )
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("PRAGMA synchronous=FULL")
            return db
        except sqlite3.Error as exc:
            raise ChallengeHold("synthetic_ledger_unavailable") from exc

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def issue_for_test(self, nonce: str, *, issued_ms: int,
                       expires_ms: int) -> None:
        _check_nonce(nonce)
        if (type(issued_ms) is not int or type(expires_ms) is not int
                or issued_ms < 0 or not 0 < expires_ms - issued_ms <= MAX_ISSUANCE_LIFETIME_MS):
            raise ChallengeHold("synthetic_challenge_window_invalid")
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO challenges(nonce,issued_ms,expires_ms) VALUES(?,?,?)",
                    (nonce, issued_ms, expires_ms),
                )
                db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            raise ChallengeHold("synthetic_challenge_already_issued") from exc
        except sqlite3.Error as exc:
            raise ChallengeHold("synthetic_ledger_unavailable") from exc

    def consume_after_verification(
        self, raw: bytes, *, expected_nonce: str, now_ms: int,
        verify_signed: Callable[..., dict],
        synthetic_external_reservation: Callable[[str, str, int], dict] | None = None,
        synthetic_anchor_public_key_sha256: str | None = None,
    ) -> dict:
        """Verify first, then one atomic spent transition.

        WARNING: this cannot grant admission. verify_signed must be a
        separately reviewed verifier, not a boolean provided by a caller.
        The fixture never generates a model or authorizes graph changes.
        """
        _check_nonce(expected_nonce)
        if (type(now_ms) is not int or now_ms < 0
                or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RECEIPT_BYTES):
            raise ChallengeHold("synthetic_receipt_invalid")
        # Verify cryptography + observation truth *before* any state write.
        proof = verify_signed(raw, expected_challenge_hex=expected_nonce, now_ms=now_ms)
        if (not isinstance(proof, dict)
                or proof.get("kind")
                != "k2-physical-witness-offline-verification-research-v1"
                or proof.get("signature_valid") is not True
                or proof.get("challenge_bound") is not True
                or proof.get("nonce_one_time_consumption_verified") is not False
                or proof.get("can_dispatch") is not False
                or proof.get("independent_host_custody_verified") is not False
                or proof.get("operator_approval_verified") is not False
                or proof.get("decision") != "HOLD"):
            raise ChallengeHold("synthetic_non_admitting_verification_required")
        try:
            document = json.loads(raw)
            issued = document["issued_at_ms"]
            expires = document["expires_at_ms"]
        except (ValueError, TypeError, KeyError) as exc:
            raise ChallengeHold("synthetic_signed_receipt_unreadable") from exc
        if (type(issued) is not int or type(expires) is not int
                or issued > now_ms or expires <= now_ms):
            raise ChallengeHold("synthetic_receipt_window_invalid")
        sha = hashlib.sha256(raw).hexdigest()
        if not SHA.fullmatch(sha):
            raise ChallengeHold("synthetic_receipt_hash_invalid")
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT issued_ms,expires_ms,spent_ms "
                    "FROM challenges WHERE nonce=?", (expected_nonce,)
                ).fetchone()
                if row is None:
                    raise ChallengeHold("synthetic_challenge_not_preissued")
                challenge_issued, challenge_expires, spent = row
                if spent is not None:
                    raise ChallengeHold("synthetic_challenge_replayed")
                if (challenge_issued > issued or challenge_expires < expires
                        or now_ms >= challenge_expires):
                    raise ChallengeHold("synthetic_challenge_window_mismatch")
                old_sequence = db.execute(
                    "SELECT sequence FROM highwater WHERE id=1"
                ).fetchone()
                if old_sequence is None or type(old_sequence[0]) is not int:
                    raise ChallengeHold("synthetic_highwater_missing")
                next_sequence = old_sequence[0] + 1
                # Optional test-only separate high-water: reserve before
                # local commit. If remote outcome is lost, DO NOT retry
                # or assume an unspent nonce. Prefer safe denial.
                if synthetic_external_reservation is not None:
                    if (not isinstance(synthetic_anchor_public_key_sha256, str)
                            or not SHA.fullmatch(synthetic_anchor_public_key_sha256)):
                        raise ChallengeHold("synthetic_external_anchor_key_not_pinned")
                    anchored = synthetic_external_reservation(
                        expected_nonce, sha, old_sequence[0]
                    )
                    expected_fields = {
                        "kind", "sequence", "nonce", "receipt_sha256",
                        "operator_signed", "independent_custody_verified",
                        "can_dispatch", "decision",
                        "test_public_key_b64", "test_signature_b64",
                    }
                    if (not isinstance(anchored, dict)
                            or set(anchored) != expected_fields
                            or anchored.get("kind")
                            != "synthetic-untrusted-highwater-reservation-v1"
                            or type(anchored.get("sequence")) is not int
                            or anchored["sequence"] != next_sequence
                            or anchored.get("nonce") != expected_nonce
                            or anchored.get("receipt_sha256") != sha
                            or anchored.get("operator_signed") is not False
                            or anchored.get("independent_custody_verified") is not False
                            or anchored.get("can_dispatch") is not False
                            or anchored.get("decision") != "HOLD"):
                        raise ChallengeHold("synthetic_external_anchor_evidence_invalid")
                    try:
                        pub64 = anchored["test_public_key_b64"]
                        sig64 = anchored["test_signature_b64"]
                        public = base64.b64decode(pub64, validate=True)
                        sig = base64.b64decode(sig64, validate=True)
                        if (len(public) != 32 or len(sig) != 64
                                or base64.b64encode(public).decode() != pub64
                                or base64.b64encode(sig).decode() != sig64
                                or hashlib.sha256(public).hexdigest()
                                != synthetic_anchor_public_key_sha256):
                            raise ChallengeHold("synthetic_external_anchor_signer_not_pinned")
                        unsigned = {k: v for k, v in anchored.items()
                                    if k != "test_signature_b64"}
                        Ed25519PublicKey.from_public_bytes(public).verify(
                            sig,
                            json.dumps(unsigned, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode()
                        )
                    except ChallengeHold:
                        raise
                    except (InvalidSignature, ValueError, TypeError) as exc:
                        raise ChallengeHold("synthetic_external_anchor_signature_invalid") from exc
                changed = db.execute(
                    "UPDATE challenges SET spent_ms=?,spent_receipt_sha256=?,sequence=? "
                    "WHERE nonce=? AND spent_ms IS NULL",
                    (now_ms, sha, next_sequence, expected_nonce),
                )
                if changed.rowcount != 1:
                    raise ChallengeHold("synthetic_challenge_race_lost")
                advanced = db.execute(
                    "UPDATE highwater SET sequence=? WHERE id=1 AND sequence=?",
                    (next_sequence, old_sequence[0]),
                )
                if advanced.rowcount != 1:
                    raise ChallengeHold("synthetic_highwater_race_lost")
                db.execute("COMMIT")
        except sqlite3.Error as exc:
            raise ChallengeHold("synthetic_ledger_unavailable") from exc
        # An atomic test fixture operation is not independent operator custody.
        return {
            "kind": "synthetic-challenge-consumption-fixture-v1",
            "sequence": next_sequence,
            "challenge_sha256": hashlib.sha256(bytes.fromhex(expected_nonce)).hexdigest(),
            "receipt_sha256": sha,
            "challenge_consumed_in_fixture": True,
            "synthetic_external_anchor_reserved": (
                synthetic_external_reservation is not None
            ),
            "independent_custody_verified": False,
            "operator_approval_verified": False,
            "can_dispatch": False,
            "decision": "HOLD",
        }

    def audit_counts(self) -> dict:
        with self._session() as db:
            row = db.execute(
                "SELECT sequence FROM highwater WHERE id=1"
            ).fetchone()
            if row is None or type(row[0]) is not int:
                raise ChallengeHold("synthetic_highwater_missing")
            return {
                "issued": db.execute("SELECT count(*) FROM challenges").fetchone()[0],
                "spent": db.execute(
                    "SELECT count(*) FROM challenges WHERE spent_ms IS NOT NULL"
                ).fetchone()[0],
                "highwater": row[0],
            }
