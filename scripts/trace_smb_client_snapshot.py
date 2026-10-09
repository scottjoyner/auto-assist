#!/usr/bin/env python3
"""Read-only client mount probe. Never prints credentials from mount options."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path


def observe(path: str) -> dict:
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("client_target_not_a_directory")
    result = subprocess.run(
        ["findmnt", "-rn", "-T", str(root), "-o", "FSTYPE,SOURCE,TARGET,OPTIONS"],
        capture_output=True,
        text=True,
        check=False,
        timeout=6,
    )
    if result.returncode:
        raise RuntimeError("findmnt_unavailable")
    cifs = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4 and parts[0] == "cifs":
            options = dict(
                (item.split("=", 1)[0], item.split("=", 1)[1]) for item in parts[3].split(",") if "=" in item
            )
            cifs.append((parts[1], parts[2], options))
    if len(cifs) != 1:
        raise RuntimeError("no_unique_cifs_mount")
    source, target, options = cifs[0]
    st = root.stat()
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError("destination_not_a_directory")
    return {
        "fstype": "cifs",
        "source": source,
        "target": target,
        "file_mode": options.get("file_mode", ""),
        "dir_mode": options.get("dir_mode", ""),
        "destination_mode": "0" + format(st.st_mode & 0o777, "03o"),
        "mounted": "yes",
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: trace_smb_client_snapshot.py CLIENT_DIRECTORY")
    try:
        print(json.dumps(observe(sys.argv[1]), sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        print(json.dumps({"status": "DENIED", "error": type(exc).__name__}), file=sys.stderr)
        sys.exit(2)
