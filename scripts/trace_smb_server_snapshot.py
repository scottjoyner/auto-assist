#!/usr/bin/env python3
"""Read-only Samba server evidence from testparm, findmnt, stat and getfacl.

Run on the Samba server with: python3 - <share-name> <export-path>
Never logs credentials or mutates the share. Output only selected policy facts.
"""

from __future__ import annotations

import configparser
import json
import pwd
import subprocess
import sys
from pathlib import Path


def run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=8)
    if result.returncode:
        raise RuntimeError("read_only_evidence_command_failed")
    return result.stdout


def observe(share_name: str, export_path: str) -> dict:
    if share_name not in {"fileserver", "assistx-trace-custody"}:
        raise ValueError("unexpected_share_name")
    root = Path(export_path)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe_share_root")
    smb = configparser.ConfigParser(interpolation=None, strict=False)
    smb.read_string(run(["testparm", "-s"]))
    share = smb[share_name] if smb.has_section(share_name) else {}
    selected = (
        "path",
        "create mask",
        "directory mask",
        "valid users",
        "force user",
        "guest ok",
        "read only",
        "wide links",
        "follow symlinks",
        "force create mode",
        "force directory mode",
    )
    share_data = {key: str(share.get(key, "")) for key in selected}
    share_data["name"] = share_name
    mount = run(["findmnt", "-rn", "-T", str(root), "-o", "TARGET,FSTYPE,UUID"]).splitlines()
    if len(mount) != 1:
        raise RuntimeError("mount_identity_ambiguous")
    row = mount[0].split()
    if len(row) != 3:
        raise RuntimeError("mount_identity_unavailable")
    st = root.stat()
    acl_lines = run(["getfacl", "-cp", str(root)]).splitlines()
    parsed = [item for item in acl_lines if item and not item.startswith("#")]
    acl = dict(
        item.split(":", 2)[0:1] + [item.rsplit(":", 1)[-1]]
        for item in parsed
        if item.startswith(("user::", "group::", "other::"))
    )
    extras = [item for item in parsed if item and not item.startswith(("user::", "group::", "other::"))]
    return {
        "hostname": run(["hostname"]).strip(),
        "capture": "testparm-findmnt-stat-getfacl",
        "mount_target": row[0],
        "mount_fstype": row[1],
        "mount_uuid": row[2],
        "share": share_data,
        "directory": {
            "mode": "0" + format(st.st_mode & 0o777, "03o"),
            "owner": pwd.getpwuid(st.st_uid).pw_name,
            "acl_user": acl.get("user", ""),
            "acl_group": acl.get("group", ""),
            "acl_other": acl.get("other", ""),
            "acl_extra": "none" if not extras else "present",
        },
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: trace_smb_server_snapshot.py SHARE ABSOLUTE_EXPORT_PATH")
    try:
        print(json.dumps(observe(sys.argv[1], sys.argv[2]), sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError, RuntimeError, KeyError) as exc:
        print(json.dumps({"status": "DENIED", "error": type(exc).__name__}), file=sys.stderr)
        sys.exit(2)
