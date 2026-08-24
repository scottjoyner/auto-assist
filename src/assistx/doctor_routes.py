"""Serve deployment-conformance findings from the FLEET-STATE doctor report."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

FLEET_STATE = Path(os.getenv(
    "ASSISTX_FLEET_STATE_DIR",
    "/media/scott/SSD_4TB/hermes-home/FLEET-STATE",
))


def build_doctor_router() -> APIRouter:
    router = APIRouter(prefix="/api/doctor", tags=["doctor"])

    @router.get("/report")
    def report() -> dict:
        path = FLEET_STATE / "doctor-report.json"
        try:
            data = json.loads(path.read_text())
            return {"ok": True, **data}
        except FileNotFoundError:
            return {"ok": False, "detail": "no doctor report; run assistx-doctor.py"}
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "detail": f"unreadable report: {exc}"}

    return router
