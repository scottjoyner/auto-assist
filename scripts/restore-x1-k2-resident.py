#!/usr/bin/env python3
"""Resource-gated, non-admitting recovery for the reviewed x1 K2 resident model."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

HOME = Path("/home/scott")
MODEL = HOME / ".lmstudio/models/IFM/K2-Horizon-0.9B-GGUF/K2-Horizon-1B-BF16.gguf"
SHA256 = "371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8"
MODEL_ID = "k2-horizon-0.9b"
VERSION = "0.4.26+4"
LMS = HOME / ".lmstudio/bin/lms"
HOLD = HOME / ".config/assistx-fleet/hold-resident-k2"
LOCK = HOME / ".local/state/assistx-admission-preflight/restore-resident-k2.lock"


def models():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:1234/v1/models", timeout=6) as response:
        if response.status != 200:
            raise ValueError("server health not OK")
        body = response.read(128_001)
    if len(body) > 128_000:
        raise ValueError("server response too large")
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("model inventory invalid")
    return [r.get("id") for r in data["data"] if isinstance(r, dict) and isinstance(r.get("id"), str)]


def available_memory_bytes():
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable not reported")


def main():
    if HOLD.exists():
        print("K2_RESTORE_HELD: operator hold active")
        return 0
    LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        loaded = models()
        if MODEL_ID in loaded:
            print("K2_RESIDENT_OK: expected model already loaded")
            return 0
        if loaded:
            print("K2_RESTORE_HELD: another model is resident")
            return 0
        if available_memory_bytes() < 8 * 1024**3:
            print("K2_RESTORE_BLOCKED: low memory", file=sys.stderr)
            return 2
        st = os.statvfs("/")
        if st.f_bavail * st.f_frsize < 10 * 1024**3:
            print("K2_RESTORE_BLOCKED: low root free space", file=sys.stderr)
            return 2
        if not MODEL.is_file() or not LMS.is_file():
            print("K2_RESTORE_BLOCKED: expected model or CLI absent", file=sys.stderr)
            return 2
        version = json.loads(Path("/opt/LM-Studio/resources/app/package.json").read_text()).get("version")
        if version != VERSION:
            print("K2_RESTORE_BLOCKED: LM Studio version changed", file=sys.stderr)
            return 2
        sha = hashlib.sha256()
        with MODEL.open("rb") as source:
            for chunk in iter(lambda: source.read(8 * 1024**2), b""):
                sha.update(chunk)
        if sha.hexdigest() != SHA256:
            print("K2_RESTORE_BLOCKED: model fingerprint mismatch", file=sys.stderr)
            return 2
        env = dict(os.environ)
        env["HOME"] = str(HOME)
        try:
            result = subprocess.run(
                [str(LMS), "load", MODEL_ID, "--identifier", MODEL_ID,
                 "--context-length", "64000", "--parallel", "4", "--yes"],
                env=env, capture_output=True, text=True, timeout=110, check=False,
            )
        except subprocess.TimeoutExpired:
            print("K2_RESTORE_BLOCKED: load timeout", file=sys.stderr)
            return 2
        if result.returncode != 0 or MODEL_ID not in models():
            print("K2_RESTORE_BLOCKED: load or postload check failed", file=sys.stderr)
            return 2
        print("K2_RESIDENT_RESTORED: loaded only after explicit physical checks; admission unchanged")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, urllib.error.URLError, BlockingIOError) as exc:
        print("K2_RESTORE_BLOCKED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(2)
