"""Opt-in, rootless, bounded Unix listener for Hermes broker *research*.

NOT a production daemon, service entrypoint or supported deployment route.
No credentials are read, and no Neo4j connection is created here. The
caller provides a trusted internal session factory. All receipts remain HOLD.

Private socket layout (created externally in isolated tests only):
- parent directory owned by broker UID, grouped to the client GID
- parent mode 02710 (owner rwx; group traverse, no group listing or writes)
- Unix socket mode 0660, same owner/group
- client UID differs from broker UID and peer UID is checked by SO_PEERCRED.
Neither this file nor CI changes an existing production path or UID.
"""
from __future__ import annotations

import os
import socket
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .hermes_broker_unix_boundary_research import (
    CandidateLocalTransportError, _peer_credentials,
    serve_one_candidate_connection, send_holding_reply,
)

_DIR_MODE = 0o2710
_SOCK_MODE = 0o660
_MAX_CONNECTIONS = 8
_MAX_LIFETIME = 30.0


class CandidateBrokerListenerError(ValueError):
    """Sanitized failure: never includes paths, peers, commands or secrets."""


def _validate_directory(directory: Path, *, client_gid: int) -> None:
    if not directory.is_absolute() or directory == Path("/"):
        raise CandidateBrokerListenerError("unsafe_socket_directory")
    if directory.is_symlink():
        raise CandidateBrokerListenerError("unsafe_socket_directory")
    try:
        mode=directory.lstat()
    except OSError:
        raise CandidateBrokerListenerError("socket_directory_unavailable") from None
    if (not stat.S_ISDIR(mode.st_mode)
            or mode.st_uid != os.geteuid()
            or mode.st_gid != client_gid
            or stat.S_IMODE(mode.st_mode) != _DIR_MODE):
        raise CandidateBrokerListenerError("unsafe_socket_directory")


def _validate_socket(path: Path, *, client_gid: int) -> tuple[int,int]:
    try:
        record=path.lstat()
    except OSError:
        raise CandidateBrokerListenerError("socket_permissions_unavailable") from None
    if (not stat.S_ISSOCK(record.st_mode)
            or record.st_uid != os.geteuid()
            or record.st_gid != client_gid
            or stat.S_IMODE(record.st_mode) != _SOCK_MODE):
        raise CandidateBrokerListenerError("unsafe_socket_permissions")
    return record.st_dev,record.st_ino


def serve_rootless_candidate(
    *,
    socket_dir: Path,
    allowed_peer_uid: int,
    client_gid: int,
    session_factory: Callable[[], Any],
    max_connections: int=1,
    max_lifetime_seconds: float=5.0,
    emit_holding_replies: bool=False,
) -> dict[str, Any]:
    """One bounded listener lifecycle; local UID boundary, never dispatch GO.

    Does not create/chmod/chown a directory, change a UID/GID, call exec,
    load secrets, open a network connection, or keep a permanent service.
    An externally isolated operator must prepare ownership and mount topology.
    Every accepted command still enters the existing deny-by-default handler.
    """
    if os.name!="posix" or not hasattr(socket,"SO_PEERCRED"):
        raise CandidateBrokerListenerError("linux_peercred_required")
    if os.geteuid()==0 or os.getegid()==0:
        raise CandidateBrokerListenerError("root_execution_forbidden")
    if (type(allowed_peer_uid) is not int or allowed_peer_uid <= 0
            or allowed_peer_uid == os.geteuid()
            or type(client_gid) is not int or client_gid <= 0):
        raise CandidateBrokerListenerError("unsafe_client_identity")
    if (type(max_connections) is not int
            or not 1 <= max_connections <= _MAX_CONNECTIONS
            or type(max_lifetime_seconds) not in (float,int)
            or not 0.1 <= max_lifetime_seconds <= _MAX_LIFETIME):
        raise CandidateBrokerListenerError("invalid_lifecycle_limits")
    if type(emit_holding_replies) is not bool:
        raise CandidateBrokerListenerError("invalid_receipt_configuration")
    if not callable(session_factory):
        raise CandidateBrokerListenerError("missing_internal_session_factory")
    if not isinstance(socket_dir,Path):
        raise CandidateBrokerListenerError("unsafe_socket_directory")
    _validate_directory(socket_dir,client_gid=client_gid)
    path=socket_dir/"broker.sock"
    if len(os.fsencode(path))>=100:
        raise CandidateBrokerListenerError("socket_path_too_long")
    # No unlink: existing socket, symlink or file is never replaced.
    if path.exists() or path.is_symlink():
        raise CandidateBrokerListenerError("socket_path_exists")
    deadline=time.monotonic()+float(max_lifetime_seconds)
    dev_inode=None
    good=0
    denied=0
    receipt_unconfirmed=0
    bound=False
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
            old_umask=os.umask(0o117)
            try:
                listener.bind(str(path))
                bound=True
            finally:
                os.umask(old_umask)
            # Ensure mode/owner/group exactly as configured before listen.
            dev_inode=_validate_socket(path,client_gid=client_gid)
            listener.listen(4)
            while good+denied < max_connections:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    break
                listener.settimeout(remaining)
                try:
                    conn,_=listener.accept()
                except socket.timeout:
                    break
                with conn:
                    # Independent SO_PEERCRED check gates *all* reply attempts.
                    # An unqualified peer receives only socket EOF, never a
                    # protocol response or source-generation information.
                    reply_allowed=False
                    if emit_holding_replies:
                        try:
                            _,peer_uid,_=_peer_credentials(conn)
                            reply_allowed=peer_uid==allowed_peer_uid
                        except CandidateLocalTransportError:
                            pass
                    try:
                        result=serve_one_candidate_connection(
                            conn,allowed_peer_uid=allowed_peer_uid,
                            session_factory=session_factory)
                        if result.get("decision")!="HOLD":
                            raise CandidateBrokerListenerError("unexpected_authority")
                    except CandidateLocalTransportError:
                        denied+=1
                        if reply_allowed:
                            try:
                                send_holding_reply(conn,admitted=False)
                            except CandidateLocalTransportError:
                                receipt_unconfirmed+=1
                    else:
                        # Do not reinterpret a committed DB transaction as a
                        # rejected command when socket delivery fails.
                        good+=1
                        if reply_allowed:
                            try:
                                send_holding_reply(
                                    conn,admitted=True,
                                    candidate_generation=result["candidate_generation"],
                                    reconciled=result.get("source_receipt_reconciled") is True)
                            except (CandidateLocalTransportError,KeyError,TypeError):
                                receipt_unconfirmed+=1
    finally:
        if bound and dev_inode is not None:
            try:
                current=path.lstat()
                if (stat.S_ISSOCK(current.st_mode)
                        and (current.st_dev,current.st_ino)==dev_inode):
                    path.unlink()
            except FileNotFoundError:
                pass
    return {
        "schema":1,
        "accepted_candidate_commands":good,
        "rejected_connections":denied,
        "unconfirmed_socket_replies":receipt_unconfirmed,
        "socket_replies_durable":False,
        "rootless_broker_uid_checked":True,
        "broker_exclusive_credentials_verified":False,
        "fleet_authentication_verified":False,
        "source_writer_census_complete":False,
        "independent_revocation_custody_verified":False,
        "external_effects_verified":False,
        "production_dispatch_authorized":False,
        "decision":"HOLD",
    }
