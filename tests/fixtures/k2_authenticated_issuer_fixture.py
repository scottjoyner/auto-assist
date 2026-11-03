"""Synthetic operator-authenticated K2 challenge issuer — TESTS ONLY.

Ephemeral keys and a disposable SQLite CAS live in the SAME test runner.
This does not provide independent trust, external key custody, or production
admission. No network, actual model, graph or host credentials are used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

NODE = "x1-370"
PURPOSE = "k2-physical-witness"
REQUEST_SCHEMA = "assistx.k2-challenge-issue-request.synthetic.v1"
RECEIPT_SCHEMA = "assistx.k2-challenge-issue-receipt.synthetic.v1"
REQUEST_DOMAIN = b"assistx-k2-synthetic-operator-issue-v1\0"
RECEIPT_DOMAIN = b"assistx-k2-synthetic-issuer-receipt-v1\0"
MAX_REQUEST_BYTES = 4096
MAX_AGE_MS = 120_000
MAX_LIFETIME_MS = 300_000
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_FIELDS = {
    "schema", "node", "purpose", "challenge_hex", "boot_id_sha256",
    "issued_at_ms", "expires_at_ms", "expected_sequence",
    "operator_public_key_b64", "operator_signature_b64",
}
RECEIPT_FIELDS = {
    "schema", "node", "purpose", "challenge_hex", "boot_id_sha256",
    "issued_at_ms", "expires_at_ms", "sequence", "request_sha256",
    "operator_key_sha256", "issuer_public_key_b64",
    "independent_custody_verified", "operator_approval_verified",
    "can_dispatch", "decision", "issuer_signature_b64",
}


class SyntheticIssuerHold(ValueError):
    """Non-sensitive fail-closed research error."""


def deny(code: str) -> None:
    raise SyntheticIssuerHold(code)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for k, v in pairs:
        if k in out:
            deny("ambiguous_duplicate_json_key")
        out[k] = v
    return out


def canonical(value: dict) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SyntheticIssuerHold("invalid_canonical_json") from exc


def strict_document(raw: bytes, fields: set, max_bytes: int) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= max_bytes:
        deny("document_size_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_keys,
            parse_constant=lambda _: deny("nonfinite_json_number"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyntheticIssuerHold("document_encoding_invalid") from exc
    if not isinstance(value, dict) or set(value) != fields:
        deny("document_fields_invalid")
    if raw != canonical(value):
        deny("noncanonical_json_bytes")
    return value


def decode_exact(value: object, size: int) -> bytes:
    if not isinstance(value, str) or len(value) > 128:
        deny("base64_invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise SyntheticIssuerHold("base64_invalid") from exc
    if len(raw) != size or base64.b64encode(raw).decode() != value:
        deny("base64_noncanonical")
    return raw


def public_bytes(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def make_operator_request(
    key: Ed25519PrivateKey, *, nonce: str, issued_ms: int, expires_ms: int,
    expected_sequence: int, boot_sha: str = "b" * 64,
) -> bytes:
    """Construct synthetic signed fixture input; never used by live systems."""
    request = {
        "schema": REQUEST_SCHEMA, "node": NODE, "purpose": PURPOSE,
        "challenge_hex": nonce, "boot_id_sha256": boot_sha,
        "issued_at_ms": issued_ms, "expires_at_ms": expires_ms,
        "expected_sequence": expected_sequence,
        "operator_public_key_b64": base64.b64encode(public_bytes(key)).decode(),
    }
    request["operator_signature_b64"] = base64.b64encode(
        key.sign(REQUEST_DOMAIN + canonical(request))
    ).decode()
    return canonical(request)


def verify_operator_request(raw: bytes, *, operator_key_pin: str,
                            now_ms: int) -> dict:
    if not isinstance(operator_key_pin, str) or not HEX64.fullmatch(operator_key_pin):
        deny("independent_operator_key_pin_missing")
    if type(now_ms) is not int or now_ms < 0:
        deny("issuer_clock_invalid")
    d = strict_document(raw, REQUEST_FIELDS, MAX_REQUEST_BYTES)
    if d["schema"] != REQUEST_SCHEMA or d["node"] != NODE or d["purpose"] != PURPOSE:
        deny("operator_request_wrong_domain_or_node")
    for key in ("challenge_hex", "boot_id_sha256"):
        if not isinstance(d[key], str) or not HEX64.fullmatch(d[key]):
            deny("operator_request_identity_invalid")
    issued, expires, seq = d["issued_at_ms"], d["expires_at_ms"], d["expected_sequence"]
    if (type(issued) is not int or type(expires) is not int
            or type(seq) is not int or seq < 1):
        deny("operator_request_time_sequence_invalid")
    if (issued > now_ms or now_ms - issued > MAX_AGE_MS
            or expires <= now_ms or expires <= issued
            or expires - issued > MAX_LIFETIME_MS):
        deny("operator_request_window_invalid")
    public = decode_exact(d["operator_public_key_b64"], 32)
    if hashlib.sha256(public).hexdigest() != operator_key_pin:
        deny("operator_signer_unapproved")
    signature = decode_exact(d["operator_signature_b64"], 64)
    message = {k: v for k, v in d.items() if k != "operator_signature_b64"}
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            signature, REQUEST_DOMAIN + canonical(message)
        )
    except (InvalidSignature, ValueError) as exc:
        raise SyntheticIssuerHold("operator_signature_invalid") from exc
    return d


def verify_issuer_receipt(
    raw: bytes, *, request_bytes: bytes, issuer_key_pin: str,
    operator_key_pin: str, now_ms: int,
) -> dict:
    """Offline receipt validator. Does NOT assert external custody."""
    if not isinstance(issuer_key_pin, str) or not HEX64.fullmatch(issuer_key_pin):
        deny("independent_issuer_key_pin_missing")
    request = verify_operator_request(
        request_bytes, operator_key_pin=operator_key_pin, now_ms=now_ms
    )
    d = strict_document(raw, RECEIPT_FIELDS, MAX_REQUEST_BYTES)
    if d["schema"] != RECEIPT_SCHEMA or d["node"] != NODE or d["purpose"] != PURPOSE:
        deny("issuer_receipt_wrong_domain_or_node")
    for key in ("challenge_hex", "boot_id_sha256", "issued_at_ms", "expires_at_ms"):
        if type(d[key]) is not type(request[key]) or d[key] != request[key]:
            deny("issuer_receipt_not_bound_to_request")
    if (type(d["sequence"]) is not int
            or d["sequence"] != request["expected_sequence"]
            or d["request_sha256"] != hashlib.sha256(request_bytes).hexdigest()):
        deny("issuer_receipt_cas_or_request_mismatch")
    operator = decode_exact(request["operator_public_key_b64"], 32)
    if d["operator_key_sha256"] != hashlib.sha256(operator).hexdigest():
        deny("issuer_receipt_operator_mismatch")
    if (d["independent_custody_verified"] is not False
            or d["operator_approval_verified"] is not False
            or d["can_dispatch"] is not False or d["decision"] != "HOLD"):
        deny("issuer_receipt_self_admission_forbidden")
    public = decode_exact(d["issuer_public_key_b64"], 32)
    if hashlib.sha256(public).hexdigest() != issuer_key_pin:
        deny("issuer_signer_unapproved")
    sig = decode_exact(d["issuer_signature_b64"], 64)
    unsigned = {k: v for k, v in d.items() if k != "issuer_signature_b64"}
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            sig, RECEIPT_DOMAIN + canonical(unsigned)
        )
    except (InvalidSignature, ValueError) as exc:
        raise SyntheticIssuerHold("issuer_receipt_signature_invalid") from exc
    return {
        "receipt_authenticated_in_fixture": True,
        "challenge_issued_in_fixture": True,
        "independent_custody_verified": False,
        "operator_approval_verified": False,
        "can_dispatch": False,
        "decision": "HOLD",
        "sequence": d["sequence"],
        "challenge_hex": d["challenge_hex"],
        "issued_at_ms": d["issued_at_ms"],
        "expires_at_ms": d["expires_at_ms"],
    }


def enroll_signed_issuance_for_test(
    ledger, *, request_bytes: bytes, receipt_bytes: bytes,
    issuer_key_pin: str, operator_key_pin: str, now_ms: int,
) -> dict:
    """A synthetic post-ack local enrollment. Never grants an approval."""
    proof = verify_issuer_receipt(
        receipt_bytes, request_bytes=request_bytes,
        issuer_key_pin=issuer_key_pin, operator_key_pin=operator_key_pin,
        now_ms=now_ms,
    )
    if (proof["can_dispatch"] is not False
            or proof["independent_custody_verified"] is not False):
        deny("issuer_enrollment_authority_forbidden")
    ledger.issue_for_test(
        proof["challenge_hex"], issued_ms=proof["issued_at_ms"],
        expires_ms=proof["expires_at_ms"],
    )
    return {
        "local_enrollment_complete": True,
        "synthetic_issuer_sequence": proof["sequence"],
        "independent_custody_verified": False,
        "operator_approval_verified": False,
        "can_dispatch": False,
        "decision": "HOLD",
    }


class SyntheticAuthenticatedIssuer:
    """One-time challenge CAS in a disposable test-owned SQLite file."""

    def __init__(self, path: Path, *, operator_key_pin: str,
                 test_issuer_key: Ed25519PrivateKey):
        self.path = Path(path)
        self.operator_key_pin = operator_key_pin
        self.test_issuer_key = test_issuer_key

    @property
    def test_issuer_key_pin(self) -> str:
        return hashlib.sha256(public_bytes(self.test_issuer_key)).hexdigest()

    @classmethod
    def bootstrap_new(
        cls, path: Path, *, operator_key_pin: str,
    ) -> SyntheticAuthenticatedIssuer:
        path = Path(path)
        if not isinstance(operator_key_pin, str) or not HEX64.fullmatch(operator_key_pin):
            deny("independent_operator_key_pin_missing")
        if path.is_symlink() or path.exists() or not path.parent.is_dir():
            deny("issuer_file_exists_or_invalid")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            raise SyntheticIssuerHold("issuer_create_failed") from exc
        os.close(fd)
        issuer = cls(
            path, operator_key_pin=operator_key_pin,
            test_issuer_key=Ed25519PrivateKey.generate(),
        )
        with issuer._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE highwater(id INTEGER PRIMARY KEY CHECK(id=1), "
                       "sequence INTEGER NOT NULL CHECK(sequence>=0))")
            db.execute("INSERT INTO highwater(id,sequence) VALUES(1,0)")
            db.execute("""
                CREATE TABLE issued(
                    nonce TEXT PRIMARY KEY CHECK(length(nonce)=64),
                    sequence INTEGER UNIQUE NOT NULL,
                    request_sha256 TEXT UNIQUE NOT NULL,
                    issued_ms INTEGER NOT NULL,
                    expires_ms INTEGER NOT NULL
                )
            """)
        return issuer

    @contextmanager
    def _session(self):
        if self.path.is_symlink() or not self.path.is_file():
            deny("issuer_missing_or_symlink")
        try:
            db = sqlite3.connect(
                self.path.absolute().as_uri() + "?mode=rw",
                uri=True, isolation_level=None, timeout=3.0,
            )
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=3000")
        except sqlite3.Error as exc:
            raise SyntheticIssuerHold("issuer_unavailable") from exc
        try:
            yield db
        finally:
            db.close()

    def issue(self, raw: bytes, *, now_ms: int) -> bytes:
        # Authentication must happen before BEGIN IMMEDIATE or any mutation.
        req = verify_operator_request(
            raw, operator_key_pin=self.operator_key_pin, now_ms=now_ms
        )
        try:
            with self._session() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT sequence FROM highwater WHERE id=1").fetchone()
                if row is None or type(row[0]) is not int:
                    deny("issuer_highwater_missing")
                if req["expected_sequence"] != row[0] + 1:
                    deny("issuer_stale_or_competing_sequence")
                if db.execute(
                    "SELECT 1 FROM issued WHERE nonce=?", (req["challenge_hex"],)
                ).fetchone():
                    deny("issuer_nonce_replayed")
                sequence = row[0] + 1
                db.execute(
                    "INSERT INTO issued(nonce,sequence,request_sha256,issued_ms,expires_ms) "
                    "VALUES (?,?,?,?,?)",
                    (req["challenge_hex"], sequence, hashlib.sha256(raw).hexdigest(),
                     req["issued_at_ms"], req["expires_at_ms"]),
                )
                changed = db.execute(
                    "UPDATE highwater SET sequence=? WHERE id=1 AND sequence=?",
                    (sequence, row[0]),
                )
                if changed.rowcount != 1:
                    deny("issuer_highwater_race")
                db.execute("COMMIT")
        except sqlite3.Error as exc:
            raise SyntheticIssuerHold("issuer_cas_unavailable") from exc

        response = {
            "schema": RECEIPT_SCHEMA, "node": NODE, "purpose": PURPOSE,
            "challenge_hex": req["challenge_hex"],
            "boot_id_sha256": req["boot_id_sha256"],
            "issued_at_ms": req["issued_at_ms"],
            "expires_at_ms": req["expires_at_ms"],
            "sequence": sequence,
            "request_sha256": hashlib.sha256(raw).hexdigest(),
            "operator_key_sha256": self.operator_key_pin,
            "issuer_public_key_b64": base64.b64encode(
                public_bytes(self.test_issuer_key)
            ).decode(),
            "independent_custody_verified": False,
            "operator_approval_verified": False,
            "can_dispatch": False,
            "decision": "HOLD",
        }
        response["issuer_signature_b64"] = base64.b64encode(
            self.test_issuer_key.sign(RECEIPT_DOMAIN + canonical(response))
        ).decode()
        return canonical(response)

    def audit(self) -> dict:
        with self._session() as db:
            row = db.execute("SELECT sequence FROM highwater WHERE id=1").fetchone()
            if row is None:
                deny("issuer_highwater_missing")
            return {
                "highwater": row[0],
                "issued": db.execute("SELECT COUNT(*) FROM issued").fetchone()[0],
            }
