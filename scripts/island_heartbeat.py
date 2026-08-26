#!/usr/bin/env python3
"""Push island recovery telemetry to the control room hub.

Reads local state only: degraded API (loopback), carve offsets on the NFS
tank, container health via the rootless docker socket. Posts once and exits
(systemd timer drives cadence). Fails soft — the hub renders stale.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

HUB_URL = os.environ.get("CONTROL_ROOM_HUB_URL", "").rstrip("/")
TOKEN = os.environ.get("CONTROL_ROOM_ISLAND_TOKEN", "")
ISLAND_API = os.environ.get("RECOVERY_SNAPSHOT_TARGET_URL", "http://127.0.0.1:27900")
AUTH_USER = os.environ.get("BASIC_AUTH_USER", "recovery-admin")
AUTH_PASS = os.environ.get("BASIC_AUTH_PASS", "")
CARVE_A = os.environ.get("CARVE_A_OFFSET", "/mnt/recover/carved_offset.txt")
CARVE_B = os.environ.get("CARVE_B_OFFSET", "/mnt/recover/carved_b_offset.txt")
TIB = 1024 ** 4


def read_int(path: str) -> int | None:
    try:
        return int(open(path).read().strip())
    except Exception:
        return None


def degraded_status() -> dict:
    req = urllib.request.Request(f"{ISLAND_API}/api/degraded/status")
    token = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.load(resp)


def container_summary() -> str:
    env = dict(os.environ, DOCKER_HOST="unix:///run/user/997/docker.sock")
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10, env=env,
        ).stdout
        lines = [l for l in out.strip().splitlines() if "assistx" in l or "falkordb" in l]
        healthy = sum(1 for l in lines if "unhealthy" not in l)
        return f"{healthy}/{len(lines)}"
    except Exception:
        return "?"


def warm_state() -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "assistx-degraded-warm.service"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out or "?"
    except Exception:
        return "?"


def device_size() -> int | None:
    try:
        return int(subprocess.run(
            ["lsblk", "-bno", "SIZE", "/dev/sda2"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip())
    except Exception:
        return None


def carvers_active() -> int:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "carver.service", "carver-b.service"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return out.split().count("active")
    except Exception:
        return -1


def carve_payload(dev_size: int | None) -> dict:
    payload = {"carvers_active": carvers_active()}
    if dev_size:
        payload["carve_device_tb"] = round(dev_size / TIB, 2)
    for key, path in (("a", CARVE_A), ("b", CARVE_B)):
        pos = read_int(path)
        if pos is None:
            continue
        rate_dir = os.environ.get("ASSISTX_RATE_DIR", "/var/lib/assistx-recovery/state")
        history = os.path.join(rate_dir, f"carve_{key}_rate.json")
        rate_bps = 0
        try:
            prev = json.load(open(history))
            dt = time.time() - float(prev["t"])
            if dt > 0:
                rate_bps = max(0, int((pos - int(prev["pos"])) / dt))
        except Exception:
            pass
        try:
            json.dump({"t": time.time(), "pos": pos}, open(history, "w"))
        except Exception:
            pass
        payload[f"carve_{key}_tb"] = round(pos / TIB, 2)
        payload[f"carve_{key}_rate"] = int(rate_bps / 1048576)
        payload[f"carve_{key}_pct"] = (
            round(pos * 100.0 / dev_size, 1) if dev_size else None
        )
        if dev_size and rate_bps > 0:
            remaining = max(0, dev_size - pos)
            payload[f"carve_{key}_eta_h"] = round(remaining / rate_bps / 3600, 1)
    return payload


def main() -> int:
    if not HUB_URL or not TOKEN:
        print("CONTROL_ROOM_HUB_URL / CONTROL_ROOM_ISLAND_TOKEN not set", file=sys.stderr)
        return 0
    payload: dict = {"host": os.uname().nodename.lower(), "sent_at_ms": int(time.time() * 1000)}
    try:
        status = degraded_status()
        journal = status.get("journal") or {}
        projection = status.get("runtime_projection") or {}
        payload["mode"] = status.get("mode")
        payload["journal_entries"] = journal.get("entries")
        payload["journal_pending"] = journal.get("pending")
        gen = projection.get("generation")
        payload["projection_generation"] = gen
        expires = projection.get("expires_at_ms")
        payload["projection_expires_in_s"] = (
            max(0, int(expires - time.time() * 1000) // 1000) if expires else None
        )
    except Exception as exc:
        payload["mode"] = f"unreachable ({str(exc)[:60]})"
    payload["warm_state"] = warm_state()
    payload["containers"] = container_summary()
    payload.update(carve_payload(device_size()))
    payload["replication_pending"] = payload.get("journal_pending")
    req = urllib.request.Request(
        f"{HUB_URL}/api/control-room/island-heartbeat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Island-Token": TOKEN},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        print(json.dumps(json.load(resp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
