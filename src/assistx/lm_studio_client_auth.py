"""LM Studio bearer handling for the fleet node agent.

Existing installations remain in compatible legacy mode until explicitly
setting FLEET_LM_AUTH_POLICY=required. In required mode, missing/unsafe
credentials fail closed before model discovery or generation calls.
Other AssistX/router destinations keep their existing authentication.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9._~+/=:-]{12,512}$")
_ALLOWED_PATH_PREFIXES = ("/v1/", "/api/v0/", "/api/v1/")


class LMStudioAuthHold(Exception):
    """The LM Studio authorization precondition is not satisfied."""


def _read_token_file(path: str) -> str:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise LMStudioAuthHold("lmstudio_token_path_required")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise LMStudioAuthHold("lmstudio_token_owner_or_type_invalid")
        if info.st_mode & 0o077 or not info.st_mode & stat.S_IRUSR:
            raise LMStudioAuthHold("lmstudio_token_mode_invalid")
        if not 12 <= info.st_size <= 514:
            raise LMStudioAuthHold("lmstudio_token_size_invalid")
        data = os.read(fd, 515)
        if len(data) != info.st_size:
            raise LMStudioAuthHold("lmstudio_token_changed")
        try:
            token = data.decode("ascii").removesuffix("\n")
        except UnicodeError:
            raise LMStudioAuthHold("lmstudio_token_encoding_invalid") from None
        if not _TOKEN_SHAPE.fullmatch(token):
            raise LMStudioAuthHold("lmstudio_token_format_invalid")
        return token
    except OSError:
        raise LMStudioAuthHold("lmstudio_token_unavailable") from None
    finally:
        if fd is not None:
            os.close(fd)


def headers_for_request(
    url: str, auth: tuple[str, str] | None, existing: dict[str, str] | None,
    *, environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Attach bearer only to local model APIs, never AssistX or router hosts.

    Default legacy preserves deployments until coordinated migration.
    Once configured required, missing/invalid token causes a hard failure;
    it does not reattempt without authorization or publish false zero models.
    """
    headers = dict(existing or {})
    if not isinstance(url, str):
        raise LMStudioAuthHold("lmstudio_url_invalid")
    try:
        target = urlsplit(url)
        port = target.port
    except ValueError:
        raise LMStudioAuthHold("lmstudio_url_invalid") from None
    if target.hostname not in {"127.0.0.1", "localhost"} or port != 1234:
        return headers
    if (target.scheme != "http" or target.username is not None
            or target.password is not None or target.query or target.fragment
            or not target.path.startswith(_ALLOWED_PATH_PREFIXES)):
        raise LMStudioAuthHold("lmstudio_endpoint_not_allowed")
    if auth or any(key.casefold() == "authorization" for key in headers):
        raise LMStudioAuthHold("lmstudio_conflicting_auth")
    env = environment if environment is not None else os.environ
    policy = env.get("FLEET_LM_AUTH_POLICY", "legacy")
    token_file = env.get("FLEET_LM_TOKEN_FILE")
    if policy == "legacy" and not token_file:
        return headers
    if policy != "required":
        raise LMStudioAuthHold("lmstudio_auth_policy_invalid")
    token = _read_token_file(token_file or "")
    headers["Authorization"] = "Bearer " + token
    return headers
