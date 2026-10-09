"""Read-only, deny-only SMB custody readiness assessment.

Nothing here provisions SMB, authorizes production, or touches a trace journal.
Server facts MUST be gathered independently on the SMB server via testparm,
findmnt, stat and getfacl; client evidence alone is not authoritative.
"""

from __future__ import annotations

import re
from typing import Any


def _mode(value: Any) -> int | None:
    if not isinstance(value, str) or not re.fullmatch(r"0?[0-7]{3,4}", value):
        return None
    return int(value, 8)


def _value(mapping: Any, name: str) -> str:
    if not isinstance(mapping, dict):
        return ""
    value = mapping.get(name)
    return value.strip() if isinstance(value, str) else ""


def evaluate(
    observation: dict[str, Any],
    *,
    expected_server_hostname: str,
    expected_server_uuid: str,
    expected_server_mount: str,
    expected_share: str,
    expected_server_path: str,
    expected_client_source: str,
    expected_client_target: str,
    dedicated_user: str,
) -> dict[str, Any]:
    """Return explicit denial reasons; never return production authorization.

    All inputs are untrusted *read-only observations*. A positive result is
    only eligibility for a bounded, operator-supervised CIFS write test.
    """
    server = observation.get("server", {}) if isinstance(observation, dict) else {}
    share = server.get("share", {}) if isinstance(server, dict) else {}
    directory = server.get("directory", {}) if isinstance(server, dict) else {}
    client = observation.get("client", {}) if isinstance(observation, dict) else {}
    reasons: list[str] = []

    def require(condition: bool, reason: str) -> None:
        if not condition:
            reasons.append(reason)

    require(_value(server, "hostname").lower() == expected_server_hostname.lower(), "SERVER_ID_UNVERIFIED")
    require(_value(server, "mount_fstype") == "btrfs", "SERVER_FS_UNVERIFIED")
    require(_value(server, "mount_uuid").lower() == expected_server_uuid.lower(), "SERVER_VOLUME_UUID_MISMATCH")
    require(_value(server, "mount_target") == expected_server_mount, "SERVER_MOUNT_TARGET_MISMATCH")
    require(_value(server, "capture") == "testparm-findmnt-stat-getfacl", "SERVER_EVIDENCE_INCOMPLETE")
    require(_value(share, "name") == expected_share, "SHARE_NAME_MISMATCH")
    require(_value(share, "path") == expected_server_path, "SHARE_PATH_MISMATCH")

    # SMB masks are maximum effective permissions; never infer actual mode
    # from these alone. The server-side stat+ACL observations are mandatory.
    require(_mode(_value(share, "create mask")) == 0o600, "SMB_CREATE_MASK_PERMISSIVE")
    require(_mode(_value(share, "directory mask")) == 0o700, "SMB_DIRECTORY_MASK_PERMISSIVE")
    require(_value(share, "guest ok").lower() in {"no", "false"}, "SMB_GUEST_NOT_EXPLICITLY_DENIED")
    require(_value(share, "read only").lower() in {"no", "false"}, "SMB_SHARE_NOT_WRITABLE")
    require(_value(share, "force user") == dedicated_user, "SMB_NONDEDICATED_UNIX_OWNER")
    valid_users = {value for value in re.split(r"[\s,]+", _value(share, "valid users")) if value}
    require(valid_users == {dedicated_user}, "SMB_USER_ACCESS_NOT_ISOLATED")
    for option in ("wide links", "follow symlinks"):
        require(_value(share, option).lower() in {"no", "false"}, "SMB_UNSAFE_LINK_POLICY")
    require(_value(share, "force create mode") in {"", "0600", "600"}, "SMB_FORCE_CREATE_MODE_UNSAFE")
    require(_value(share, "force directory mode") in {"", "0700", "700"}, "SMB_FORCE_DIRECTORY_MODE_UNSAFE")

    require(_mode(_value(directory, "mode")) == 0o700, "SERVER_DIRECTORY_PERMISSIONS_UNSAFE")
    require(_value(directory, "owner") == dedicated_user, "SERVER_DIRECTORY_OWNER_WRONG")
    require(
        _value(directory, "acl_user") == "rwx"
        and _value(directory, "acl_group") == "---"
        and _value(directory, "acl_other") == "---"
        and _value(directory, "acl_extra") == "none",
        "SERVER_ACL_NOT_EXCLUSIVE",
    )

    require(_value(client, "fstype") == "cifs", "CLIENT_NOT_CIFS")
    require(_value(client, "source") == expected_client_source, "CLIENT_SHARE_SOURCE_WRONG")
    require(_value(client, "target") == expected_client_target, "CLIENT_MOUNT_TARGET_WRONG")
    require(_mode(_value(client, "file_mode")) == 0o600, "CLIENT_FILE_MODE_UNSAFE")
    require(_mode(_value(client, "dir_mode")) == 0o700, "CLIENT_DIRECTORY_MODE_UNSAFE")
    require(_mode(_value(client, "destination_mode")) == 0o700, "CLIENT_DESTINATION_MODE_UNSAFE")
    require(_value(client, "mounted") == "yes", "CLIENT_MOUNT_NOT_ACTIVE")
    # Even a positive snapshot is NOT sufficient to approve real writes:
    # server-created object mode and CIFS hardlink/fsync semantics must be
    # independently witnessed by the later bounded actual-write drill.
    return {
        "schema": "assistx.trace-smb-readiness.v1",
        "eligible_for_bounded_write_pilot": len(reasons) == 0,
        "production_custody_approved": False,
        "reason_codes": sorted(set(reasons)),
        "unresolved_even_if_eligible": [
            "REAL_CIFS_NO_OVERWRITE_AND_DIRECTORY_FSYNC_NOT_TESTED",
            "SERVER_SIDE_WRITE_ACL_NOT_NEGATIVE_TESTED",
            "INDEPENDENT_IMMUTABLE_WITNESS_NOT_PROVISIONED",
        ],
    }
