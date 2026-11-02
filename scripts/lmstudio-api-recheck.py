#!/usr/bin/env python3
"""Bounded local API availability watchdog, never load a model or admit a runtime.

Run as a separate systemd one-shot service, NOT the long-lived API starter:
the starter uses RemainAfterExit=yes to retain its launched process group.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

URL = "http://127.0.0.1:1234/v1/models"
STARTER = "lm-studio-api-start.service"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_available() -> bool:
    # Strict localhost, no environment HTTP proxy or redirects.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    try:
        with opener.open(urllib.request.Request(URL, method="GET"), timeout=3) as reply:
            if reply.status != 200:
                return False
            data = reply.read(65537)
            if len(data) > 65536:
                return False
        document = json.loads(data)
        return (
            isinstance(document, dict)
            and isinstance(document.get("data"), list)
            and document.get("error") is None
            and all(isinstance(m, dict) and isinstance(m.get("id"), str)
                    for m in document["data"])
        )
    except Exception:
        return False


def check_and_recover() -> str:
    if api_available():
        return "healthy"
    # Only the existing, exact API bootstrap unit is restarted. No provider,
    # model-loader, inference request, or approval operation occurs here.
    try:
        started = subprocess.run(
            ["systemctl", "--user", "restart", STARTER],
            capture_output=True, text=True, timeout=35, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "recovery_failed"
    if started.returncode != 0:
        return "recovery_failed"
    time.sleep(2)
    return "recovered" if api_available() else "recovery_unverified"


def main() -> int:
    if len(sys.argv) != 1:
        print("API_WATCHDOG: DENIED unexpected arguments", file=sys.stderr)
        return 2
    state = check_and_recover()
    print("API_WATCHDOG: " + state)
    return 0 if state in ("healthy", "recovered") else 1


if __name__ == "__main__":
    raise SystemExit(main())
