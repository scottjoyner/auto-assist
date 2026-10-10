"""Private Tailscale Serve ingress for redacted fleet lineage metadata.

Do not expose this application directly to LAN, WAN, or Funnel. Launch bound
to 127.0.0.1 and proxy ONLY named paths through Tailscale Serve. Serve strips
spoofable identity/capability headers; direct HTTP requests must be impossible
from untrusted clients. No trace header ever provides operational authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .fleet_trace_contract import validate_envelope

TENANT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
MAX_BODY = 8192
MAX_CAPABILITY_HEADER = 4096
MAX_DB_ROWS = 50_000
MAX_PAGE = 100
WRITER_CAPABILITY = "nez.ninja/cap/fleet-trace-write"
READER_CAPABILITY = "nez.ninja/cap/fleet-trace-read"
app = FastAPI(title="Fleet trace ingress (tailnet-only)", docs_url=None, redoc_url=None,
              openapi_url=None)


def _deny(status: int, code: str) -> None:
    raise HTTPException(status_code=status, detail=code)


def _env_policy() -> dict[str, Any]:
    # Empty or invalid configuration is deny-all. Never infer an identity from
    # an untrusted traceparent, HTTP hostname or custom X-User header.
    raw = os.environ.get("FLEET_TRACE_AUTH_POLICY_JSON", "").strip()
    if not raw or len(raw) > 16384:
        _deny(503, "ingress_policy_unavailable")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        _deny(503, "ingress_policy_unavailable")
    if not isinstance(value, dict):
        _deny(503, "ingress_policy_unavailable")
    return value


def _ident(request: Request, policy: dict[str, Any], write: bool) -> tuple[str, dict]:
    if os.environ.get("FLEET_TRACE_TRUSTED_SERVE", "") != "1":
        _deny(503, "trusted_serve_not_configured")
    login = request.headers.get("Tailscale-User-Login", "").strip().lower()
    # Registered logins are explicit authorizations, not all tailnet users.
    if login and len(login) <= 254:
        key = "login:" + login
        entry = policy.get(key)
        if isinstance(entry, dict) and (entry.get("write") is True if write
                                        else entry.get("read") is True):
            if _valid_scope(entry):
                return key, entry
    # Tagged devices do not get Tailscale-User-Login. Tailscale Serve may
    # inject app capabilities for them, and strips caller-supplied copies.
    raw = request.headers.get("Tailscale-App-Capabilities", "")
    if 0 < len(raw) <= MAX_CAPABILITY_HEADER:
        try:
            caps = json.loads(raw)
        except ValueError:
            caps = None
        if isinstance(caps, dict):
            capability = WRITER_CAPABILITY if write else READER_CAPABILITY
            grants = caps.get(capability)
            if isinstance(grants, list):
                for grant in grants:
                    if not isinstance(grant, dict):
                        continue
                    principal = grant.get("principal")
                    # The policy must independently register each machine
                    # identity and its allowed node and producer scope.
                    if isinstance(principal, str) and principal.startswith("tag:"):
                        entry = policy.get(principal)
                        if isinstance(entry, dict) and _valid_scope(entry):
                            if entry.get("write" if write else "read") is True:
                                return principal, entry
    _deny(403, "trace_principal_not_authorized")


def _valid_scope(entry: dict[str, Any]) -> bool:
    """All scopes come from trusted server-side policy, never request JSON."""
    nodes = entry.get("nodes")
    producers = entry.get("producers")
    tenant = entry.get("write_tenant")
    readable = entry.get("read_tenants")
    valid_tenant = lambda x: isinstance(x, str) and TENANT.fullmatch(x) is not None
    return (isinstance(nodes, list) and 0 < len(nodes) <= 64
            and all(isinstance(x, str) and 0 < len(x) <= 128 for x in nodes)
            and isinstance(producers, list) and 0 < len(producers) <= 12
            and all(isinstance(x, str) and 0 < len(x) <= 30 for x in producers)
            and (entry.get("write") is not True or valid_tenant(tenant))
            and (entry.get("read") is not True
                 or (isinstance(readable, list) and 0 < len(readable) <= 16
                     and len(set(readable)) == len(readable)
                     and all(valid_tenant(x) for x in readable))))



def _traceparent(request: Request) -> str:
    raw = request.headers.get("traceparent", "")
    match = TRACEPARENT.fullmatch(raw)
    if (not match or match[1] == "0" * 32 or match[2] == "0" * 16):
        _deny(400, "valid_w3c_traceparent_required")
    return match[1]


def _database() -> Path:
    raw = os.environ.get("FLEET_TRACE_DB_PATH", "")
    if not raw or not os.path.isabs(raw):
        _deny(503, "local_trace_storage_not_configured")
    path = Path(raw)
    if path.is_symlink() or not path.parent.is_dir():
        _deny(503, "local_trace_storage_not_configured")
    # The directory is provisioned explicitly before startup. It must be owned
    # by the gateway UID and inaccessible to other local users.
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        _deny(503, "local_trace_directory_permissions")
    if path.exists() and (not path.is_file() or path.stat().st_uid != os.geteuid()):
        _deny(503, "local_trace_storage_permissions")
    return path


def _connect() -> sqlite3.Connection:
    path = _database()
    db = sqlite3.connect(str(path), timeout=0.5)
    db.execute("PRAGMA busy_timeout=500")
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("""CREATE TABLE IF NOT EXISTS trace_events (
        tenant_scope TEXT NOT NULL,
        event_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        span_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        producer TEXT NOT NULL,
        principal_digest TEXT NOT NULL,
        received_at TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        envelope TEXT NOT NULL,
        PRIMARY KEY (tenant_scope, event_id)
    )""")
    # Never silently upgrade a pre-tenant database. Its earlier rows lack
    # trusted owner provenance, so assigning them a tenant would fabricate
    # authorization. Migration needs explicit custody/owner signoff.
    columns = {row[1] for row in db.execute("PRAGMA table_info(trace_events)")}
    if not {"tenant_scope", "producer"} <= columns:
        db.close()
        _deny(503, "trace_tenant_schema_migration_required")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_tenant_trace_events
                  ON trace_events(tenant_scope, trace_id, received_at, event_id)""")
    os.chmod(path, 0o600)
    return db


def _digest_principal(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


async def _read_bounded_body(request: Request) -> bytes:
    collected = bytearray()
    async for chunk in request.stream():
        if len(collected) + len(chunk) > MAX_BODY:
            _deny(413, "trace_metadata_too_large")
        collected.extend(chunk)
    return bytes(collected)


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ready_for_auth_check": True}  # NOT proof of tracing or admission


@app.post("/v1/events")
async def ingest(request: Request) -> JSONResponse:
    policy = _env_policy()
    actor, scope = _ident(request, policy, write=True)
    trace_id = _traceparent(request)
    if request.headers.get("content-type", "").lower().split(";")[0].strip() != "application/json":
        _deny(415, "json_required")
    data = await _read_bounded_body(request)
    try:
        row = validate_envelope(json.loads(data))
    except (TypeError, ValueError, UnicodeDecodeError):
        _deny(422, "invalid_redacted_trace_envelope")
    if not TRACEPARENT.fullmatch(f"00-{row['trace_id']}-{row['span_id']}-01"):
        _deny(422, "w3c_span_identifiers_required")
    if row["span_id"] == "0" * 16:
        _deny(422, "w3c_span_identifiers_required")
    if row["trace_id"] != trace_id:
        _deny(422, "traceparent_trace_mismatch")
    if row["node_id"] not in scope["nodes"] or row["producer"] not in scope["producers"]:
        _deny(403, "producer_scope_denied")
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    actor_digest = _digest_principal(actor)
    tenant_scope = scope["write_tenant"]  # server policy identity, not a client field
    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        with closing(_connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT sha256, principal_digest FROM trace_events
                   WHERE tenant_scope=? AND event_id=?""",
                (tenant_scope, row["event_id"])
            ).fetchone()
            if existing:
                if existing != (digest, actor_digest):
                    _deny(409, "conflicting_or_cross_actor_event_id")
                accepted = False
            else:
                count = db.execute("SELECT count(*) FROM trace_events").fetchone()[0]
                if count >= MAX_DB_ROWS:
                    _deny(507, "trace_ingress_capacity_limit")
                db.execute(
                    "INSERT INTO trace_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (tenant_scope, row["event_id"], trace_id, row["span_id"],
                     row["node_id"], row["producer"], actor_digest,
                     received_at, digest, canonical)
                )
                accepted = True
    except sqlite3.Error:
        _deny(503, "local_trace_storage_unavailable")
    # A locally durable SQLite receipt is NOT a signature, quorum witness or
    # evidence that downstream fleet activity occurred as described.
    return JSONResponse(
        {"accepted": accepted, "event_id": row["event_id"], "sha256": digest,
         "received_at": received_at if accepted else None,
         "custody": "local_sqlite_metadata_only", "source_attested": False},
        status_code=201 if accepted else 200,
        headers={"traceparent": request.headers["traceparent"], "Cache-Control": "no-store"}
    )


@app.get("/v1/traces/{trace_id}/events")
async def get_events(trace_id: str, request: Request, limit: int = 30) -> JSONResponse:
    policy = _env_policy()
    _, scope = _ident(request, policy, write=False)
    if (not re.fullmatch(r"[0-9a-f]{32}", trace_id)
            or trace_id == "0" * 32 or not (1 <= limit <= MAX_PAGE)):
        _deny(422, "invalid_trace_query")
    try:
        node_placeholders = ",".join("?" for _ in scope["nodes"])
        producer_placeholders = ",".join("?" for _ in scope["producers"])
        tenant_placeholders = ",".join("?" for _ in scope["read_tenants"])
        with closing(_connect()) as db:
            rows = db.execute(
                f"""SELECT envelope, received_at, sha256 FROM trace_events
                   WHERE trace_id=? AND tenant_scope IN ({tenant_placeholders})
                     AND node_id IN ({node_placeholders})
                     AND producer IN ({producer_placeholders})
                   ORDER BY received_at DESC,event_id DESC LIMIT ?""",
                (trace_id, *scope["read_tenants"], *scope["nodes"],
                 *scope["producers"], limit)
            ).fetchall()
    except sqlite3.Error:
        _deny(503, "local_trace_storage_unavailable")
    return JSONResponse(
        {"trace_id": trace_id, "events": [
            {"envelope": json.loads(row[0]), "received_at": row[1], "sha256": row[2]}
            for row in rows], "partial_history_possible": True,
         "custody": "local_sqlite_metadata_only", "source_attested": False},
        headers={"Cache-Control": "no-store"}
    )
