"""Linux-only, single-connection Unix-socket Hermes broker boundary research.

This module does NOT create/listen on a socket, start a daemon, connect to
Neo4j, provision credentials, authorize fleet dispatch or define a deployment.
The caller must provide a connected AF_UNIX socket, an immutable, operator-
selected *different OS UID* for the client, and an internal session factory.

SO_PEERCRED comes from the kernel, never from request payload. Authentication
here is a local UID check ONLY: no device grant, Tailnet credential, tenant
binding, independent replay store, restart-safe high-water, or external effect
authorization. Root/same-UID process control can defeat this boundary.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import time
from collections.abc import Callable
from typing import Any

from .hermes_broker_command_research import (
    CandidateBrokerCommandError, dispatch_candidate_command,
)
from .hermes_source_generation_research import OWNER

MAX_FRAME_BYTES=1024
MAX_DURATION_SECONDS=1.0
_VERSION=1

class CandidateLocalTransportError(ValueError):
    """Sanitized local transport rejection; no peer/request content."""

def _peer_credentials(sock: socket.socket) -> tuple[int,int,int]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise CandidateLocalTransportError("kernel_peer_identity_unsupported")
    try:
        raw=sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize("3i"))
        pid,uid,gid=struct.unpack("3i",raw)
    except (OSError,ValueError,struct.error) as exc:
        raise CandidateLocalTransportError("kernel_peer_identity_unavailable") from None
    if pid <= 0 or uid < 0 or gid < 0:
        raise CandidateLocalTransportError("kernel_peer_identity_unavailable")
    return pid,uid,gid

def _unique_json_pairs(pairs: list[tuple[str,Any]]) -> dict[str,Any]:
    output={}
    for k,v in pairs:
        if k in output:
            raise CandidateLocalTransportError("invalid_command_envelope")
        output[k]=v
    return output

def _reject_nonfinite(_value: str) -> None:
    raise CandidateLocalTransportError("invalid_command_envelope")

def _bounded_frame(sock: socket.socket, *, deadline: float) -> dict[str,Any]:
    data=bytearray()
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise CandidateLocalTransportError("request_deadline_exceeded")
        try:
            sock.settimeout(remaining)
            chunk=sock.recv(min(128,MAX_FRAME_BYTES+1-len(data)))
        except (OSError,TimeoutError) as exc:
            raise CandidateLocalTransportError("request_read_failed") from None
        if not chunk:
            raise CandidateLocalTransportError("request_truncated")
        data.extend(chunk)
        if len(data)>MAX_FRAME_BYTES:
            raise CandidateLocalTransportError("request_too_large")
        if b"\n" in chunk:
            break
    frame,delimiter,tail=bytes(data).partition(b"\n")
    if tail:
        raise CandidateLocalTransportError("multiple_commands_not_allowed")
    if not frame:
        raise CandidateLocalTransportError("invalid_command_envelope")
    try:
        obj=json.loads(frame.decode("utf-8"),
                       object_pairs_hook=_unique_json_pairs,
                       parse_constant=_reject_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise CandidateLocalTransportError("invalid_command_envelope") from None
    if type(obj) is not dict or set(obj)!={"version","command"} or obj["version"]!=_VERSION or type(obj["version"]) is not int:
        raise CandidateLocalTransportError("invalid_command_envelope")
    if type(obj["command"]) is not dict:
        raise CandidateLocalTransportError("invalid_command_envelope")
    return obj["command"]

def serve_one_candidate_connection(
    conn: socket.socket,
    *,
    allowed_peer_uid: int,
    session_factory: Callable[[],Any],
) -> dict[str,Any]:
    """Handle exactly one request; socket lifetime owned by its caller.

    The allowed UID must differ from the broker UID. SO_PEERCRED is checked
    before JSON is read or a DB session is opened. This blocks the obvious
    same-UID credential sharing pattern but isn't a sandbox for privileged
    same-host actors. The result always keeps HOLD.
    """
    if (type(allowed_peer_uid) is not int or allowed_peer_uid < 0
            or allowed_peer_uid == os.geteuid()):
        raise CandidateLocalTransportError("unsafe_peer_uid_configuration")
    if not callable(session_factory):
        raise CandidateLocalTransportError("internal_session_unavailable")
    if conn.family != socket.AF_UNIX:
        raise CandidateLocalTransportError("unix_socket_required")
    pid,uid,gid=_peer_credentials(conn)
    if uid != allowed_peer_uid:
        raise CandidateLocalTransportError("peer_not_authorized")
    command=_bounded_frame(conn,deadline=time.monotonic()+MAX_DURATION_SECONDS)
    try:
        receipt=dispatch_candidate_command(
            command, trusted_channel_principal=OWNER,
            session_factory=session_factory,
        )
    except CandidateBrokerCommandError:
        raise CandidateLocalTransportError("command_rejected") from None
    return {
        "schema":1,
        "transport_kernel_peer_uid_matched":True,
        "candidate_generation":receipt["candidate_generation"],
        "transport_fleet_authenticated":False,
        "broker_exclusive_credentials_verified":False,
        "all_writers_generation_fenced":False,
        "independent_revocation_custody_verified":False,
        "external_effects_verified":False,
        "production_dispatch_authorized":False,
        "decision":"HOLD",
    }
