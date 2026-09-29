#!/usr/bin/env python3
"""Enroll a known-speaker WAV through Sophia's voice-agent and register the
verdict with AssistX's speaker identity registry.

Usage:
  python3 scripts/enroll_speaker_backfill.py \
      --audio /path/to/speaker.wav --user-id scott \
      --assistx-url http://localhost:8000 --sophia-url http://localhost:8765

Requires ASSISTX_BASIC_AUTH_USER/PASS (or --auth-user/--auth-pass) for the
AssistX registry call. Audio must be 16-bit PCM WAV; use ffmpeg to convert
compressed sources first.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid


def http_json(url: str, payload: dict | None = None, *, auth: tuple[str, str] | None = None,
              timeout: int = 120) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET")
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", required=True, help="16-bit PCM WAV of the speaker to enroll")
    ap.add_argument("--user-id", default="scott")
    ap.add_argument("--device-id", default="backfill")
    ap.add_argument("--sophia-url", default=os.getenv("SOPHIA_URL", "http://localhost:8765"))
    ap.add_argument("--assistx-url", default=os.getenv("ASSISTX_URL", "http://localhost:8000"))
    ap.add_argument("--auth-user", default=os.getenv("BASIC_AUTH_USER", "admin"))
    ap.add_argument("--auth-pass", default=os.getenv("BASIC_AUTH_PASS", ""))
    args = ap.parse_args()

    if not os.path.isfile(args.audio):
        print(f"audio not found: {args.audio}", file=sys.stderr)
        return 2
    if not args.auth_pass:
        print("--auth-pass or BASIC_AUTH_PASS required for registry registration", file=sys.stderr)
        return 2

    # 1) Enroll through Sophia's ECAPA pipeline.
    boundary = uuid.uuid4().hex
    fname = os.path.basename(args.audio)
    audio_bytes = open(args.audio, "rb").read()
    parts = []
    for field, value in (("user_id", args.user_id), ("device_id", args.device_id), ("force", "true")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{fname}\"\r\n"
        "Content-Type: audio/wav\r\n\r\n".encode() + audio_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{args.sophia_url.rstrip('/')}/voiceprints/enroll", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        enroll = json.loads(resp.read().decode())
    if not enroll.get("ok"):
        print(f"enroll failed: {enroll}", file=sys.stderr)
        return 1
    print(f"[sophia] enrolled user_id={args.user_id} samples={enroll.get('sample_count')}")

    # 2) Self-consistency verify: same clip must be ACCEPTED as this speaker.
    req = urllib.request.Request(
        f"{args.sophia_url.rstrip('/')}/auth/verify", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        verify = json.loads(resp.read().decode())
    if not verify.get("accepted"):
        print(f"self-verify rejected (score={verify.get('score')}): aborting registration", file=sys.stderr)
        return 1
    confidence = float(verify.get("score") or 0.0)
    print(f"[sophia] self-verify accepted score={confidence}")

    # 3) Register the verdict with AssistX's speaker registry.
    reg = http_json(
        f"{args.assistx_url.rstrip('/')}/api/voice-identity/verifications",
        {"source": "sophia-voice-agent", "source_id": args.user_id,
         "confidence": confidence,
         "metadata": {"device_id": args.device_id, "enroll_event_id": enroll.get("event_id")}},
        auth=(args.auth_user, args.auth_pass),
    )
    print(f"[assistx] registered={reg.get('registered')} speaker={reg.get('speaker')}")

    # 4) Confirm trust gate flips on.
    req = urllib.request.Request(
        f"{args.assistx_url.rstrip('/')}/api/voice-identity/trust?source_id={args.user_id}"
    )
    token = base64.b64encode(f"{args.auth_user}:{args.auth_pass}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        trust = json.loads(resp.read().decode())
    print(f"[assistx] trusted={trust.get('trusted')}")
    return 0 if trust.get("trusted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
