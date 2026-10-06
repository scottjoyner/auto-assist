"""Pushcut alerting for AssistX doctor findings and critical events.

Reuses the fleet-watchdog's Pushcut API key. Posts to the named Signal Agent
endpoint (NOT generic /notifications which 404s).
"""

from __future__ import annotations

import json
import os
import urllib.request

PUSHCUT_KEY = os.getenv("PUSHCUT_API_KEY", "")
PUSHCUT_URL = "https://api.pushcut.io/{key}/notifications/Signal%20Agent"


def pushcut_notify(title: str, text: str) -> bool:
    if not PUSHCUT_KEY:
        return False
    body = json.dumps({"title": title, "text": text, "isCritical": False}).encode()
    req = urllib.request.Request(
        PUSHCUT_URL.format(key=PUSHCUT_KEY),
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False
