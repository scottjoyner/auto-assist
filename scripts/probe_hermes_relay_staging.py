#!/usr/bin/env python3
"""One-shot, opt-in Hermes relay staging canary. Never production admission.

Run INSIDE an explicitly isolated staging Compose API network only. Reads
a dedicated relay client token from environment, never argv or output.
Checks authenticated real streaming generation, unauthorized and unapproved
model rejection, and rejects ambiguous client routing and redirect behavior.
All reports are non-secret, categorical JSON; always exits 2/HOLD.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
from urllib.parse import urlsplit

BASE_PATH = "/api/internal/hermes-router/v1"
CHAT_PATH = BASE_PATH + "/chat/completions"
_ALLOWED_ORIGINS = {
    "http://assistx-api:8000",
    "http://127.0.0.1:8000",
}
_MAX_REPLY = 2 * 1024 * 1024
_MODEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:+-]{0,127}$")


class CanaryDenied(Exception):
    """Safe, nonsecret canary failure category."""


def _origin(raw: str) -> tuple[str, int]:
    try:
        parts = urlsplit(raw)
        good = (
            raw in _ALLOWED_ORIGINS
            and parts.scheme == "http"
            and parts.path == ""
            and not parts.query and not parts.fragment
            and parts.username is None and parts.password is None
            and parts.port == 8000
        )
    except (ValueError, TypeError):
        good = False
    if not good:
        raise CanaryDenied("origin_not_explicitly_isolated")
    return parts.hostname, 8000


def _read_reply(response: http.client.HTTPResponse) -> bytes:
    hint = response.getheader("Content-Length")
    if hint is not None and (not hint.isdecimal() or int(hint) > _MAX_REPLY):
        raise CanaryDenied("response_too_large")
    contents = response.read(_MAX_REPLY + 1)
    if len(contents) > _MAX_REPLY:
        raise CanaryDenied("response_too_large")
    return contents


def _parsed_stream(content: bytes) -> bool:
    """Prove an actual text delta followed by an unambiguous final DONE."""
    data = content.replace(b"\r\n", b"\n")
    segments = data.split(b"\n\n")
    if segments[-1] != b"":
        raise CanaryDenied("truncated_stream")
    frames = [segment for segment in segments[:-1] if segment]
    if not 2 <= len(frames) <= 1024:
        raise CanaryDenied("incomplete_stream")
    seen_text = False
    done = False
    for index, frame in enumerate(frames):
        if not frame.startswith(b"data: "):
            raise CanaryDenied("invalid_stream")
        payload = frame[6:]
        if payload == b"[DONE]":
            if index != len(frames) - 1:
                raise CanaryDenied("premature_done")
            done = True
            continue
        try:
            obj = json.loads(payload)
        except (UnicodeError, ValueError) as exc:
            raise CanaryDenied("invalid_json_frame") from exc
        if not isinstance(obj, dict) or not isinstance(obj.get("choices"), list):
            raise CanaryDenied("invalid_completion_schema")
        for choice in obj["choices"]:
            if not isinstance(choice, dict):
                raise CanaryDenied("invalid_choice")
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise CanaryDenied("invalid_delta")
            if isinstance(delta.get("content"), str) and delta["content"].strip():
                seen_text = True
    if not done:
        raise CanaryDenied("missing_done")
    if not seen_text:
        raise CanaryDenied("no_real_model_text")
    return True


def _request(host: str, port: int, *, token: str, model: str,
             stream: bool = True) -> tuple[int, str, bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=12)
    try:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content":
                          "Reply with one short plain-text word."}],
            "stream": stream,
            "max_tokens": 32,
            "temperature": 0,
        }, separators=(",", ":")).encode("utf-8")
        connection.request("POST", CHAT_PATH, body=body, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Connection": "close",
        })
        reply = connection.getresponse()
        status = reply.status
        media = (reply.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if 300 <= status < 400:
            raise CanaryDenied("redirect_denied")
        return status, media, _read_reply(reply)
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise CanaryDenied("transport_unavailable") from exc
    finally:
        connection.close()


def probe(*, origin: str, token: str, model: str,
          invalid_model: str = "unapproved-canary-model-v1") -> dict:
    proof = {
        "schema": "hermes.staging-inference-canary.v1",
        "read_only_canary": True,
        "nonproduction_inference_request": True,
        "model_text_and_done_verified": False,
        "invalid_client_token_denied": False,
        "unapproved_model_denied": False,
        "ingress_public_path_denial_verified": False,
        "real_hermes_process_verified": False,
        "router_admin_exposure_remediated": False,
        "production_admission": "HOLD",
        "gate": "HOLD",
    }
    try:
        host, port = _origin(origin)
        if (len(token) < 32 or len(token) > 256 or not token.isascii()
                or not _MODEL.fullmatch(model)
                or not _MODEL.fullmatch(invalid_model)
                or model == invalid_model):
            raise CanaryDenied("canary_identity_or_model_invalid")
        status, media, body = _request(
            host, port, token=token, model=model,
        )
        if status != 200 or media != "text/event-stream":
            raise CanaryDenied("model_generation_unavailable")
        proof["model_text_and_done_verified"] = _parsed_stream(body)
        # Do not ever use the upstream service token for the negative probe.
        wrong = "wrong-client-token-" + "0" * 48
        if wrong == token:
            wrong = "wrong-client-token-" + "1" * 48
        status, _media, _body = _request(
            host, port, token=wrong, model=model,
        )
        if status not in (401, 403):
            raise CanaryDenied("unauthorized_client_not_denied")
        proof["invalid_client_token_denied"] = True
        status, _media, _body = _request(
            host, port, token=token, model=invalid_model,
        )
        if status != 403:
            raise CanaryDenied("unapproved_model_not_denied")
        proof["unapproved_model_denied"] = True
        proof["observation_complete"] = True
    except CanaryDenied as exc:
        proof["observation_complete"] = False
        proof["error_code"] = str(exc)
    return proof


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--explicit-isolated-staging-only",
                        action="store_true", required=True)
    args = parser.parse_args(argv)
    _ = args
    report = probe(
        origin=os.environ.get("ASSISTX_HERMES_RELAY_STAGING_ORIGIN", ""),
        token=os.environ.get("ASSISTX_HERMES_RELAY_CLIENT_TOKEN", ""),
        model=os.environ.get("ASSISTX_HERMES_RELAY_STAGING_MODEL", ""),
    )
    print(json.dumps(report, sort_keys=True))
    return 2  # Even a positive live staging canary never approves production.


if __name__ == "__main__":
    raise SystemExit(main())
