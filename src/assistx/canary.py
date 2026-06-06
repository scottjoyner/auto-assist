from __future__ import annotations

import json
import time
from dataclasses import dataclass
from time import monotonic
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urljoin

import requests


@dataclass(frozen=True)
class SignedIngestSample:
    endpoint: str
    payload: Mapping[str, Any]
    signature_header: str
    signature: str
    auth_user: Optional[str] = None
    auth_pass: Optional[str] = None


@dataclass(frozen=True)
class CutoverCanaryTarget:
    worker_target: str
    expected_disposition: str


@dataclass(frozen=True)
class CutoverCanaryResult:
    ingest_response: Dict[str, Any]
    dispatch_response: Dict[str, Any]
    terminal_dispatch: Dict[str, Any]
    elapsed_s: float


def run_cutover_canary(
    *,
    base_url: str,
    target: CutoverCanaryTarget,
    signed_enrollment_sample: SignedIngestSample,
    timeout_s: float = 300.0,
    poll_interval_s: float = 5.0,
    session: Optional[requests.Session] = None,
) -> CutoverCanaryResult:
    client = session or requests.Session()
    start = monotonic()
    deadline = start + timeout_s
    auth = _auth_tuple(signed_enrollment_sample.auth_user, signed_enrollment_sample.auth_pass)

    ingest_url = _absolute_url(base_url, signed_enrollment_sample.endpoint)
    ingest_response = _post_json(
        client,
        ingest_url,
        signed_enrollment_sample.payload,
        headers={
            signed_enrollment_sample.signature_header: signed_enrollment_sample.signature,
            "Content-Type": "application/json",
        },
        auth=auth,
    )

    task_id = ingest_response.get("task_id")
    if not task_id:
        raise RuntimeError(f"Signed ingest did not return a task_id: {ingest_response}")

    dispatch_response = _post_json(
        client,
        _absolute_url(base_url, "/api/dispatch"),
        {
            "task_id": task_id,
            "target": {"paperclip_agent_id": target.worker_target, "capabilities": ["terminal"]},
            "priority": "HIGH",
        },
        headers={"Content-Type": "application/json"},
        auth=auth,
    )
    issue_id = dispatch_response.get("paperclip_issue_id")
    if not issue_id:
        raise RuntimeError(f"Dispatch did not return a paperclip_issue_id: {dispatch_response}")

    terminal_dispatch = _wait_for_dispatch(
        client,
        base_url=base_url,
        issue_id=issue_id,
        expected_disposition=target.expected_disposition,
        deadline=deadline,
        poll_interval_s=poll_interval_s,
        auth=auth,
    )
    return CutoverCanaryResult(
        ingest_response=ingest_response,
        dispatch_response=dispatch_response,
        terminal_dispatch=terminal_dispatch,
        elapsed_s=max(0.0, monotonic() - start),
    )


def _wait_for_dispatch(
    client: requests.Session,
    *,
    base_url: str,
    issue_id: str,
    expected_disposition: str,
    deadline: float,
    poll_interval_s: float,
    auth: Optional[tuple[str, str]],
) -> Dict[str, Any]:
    target_status = expected_disposition.strip().upper()
    while time.monotonic() < deadline:
        items = _get_json(client, _absolute_url(base_url, "/api/dispatches"), params={"limit": 200}, auth=auth).get("items", [])
        for item in items:
            if str(item.get("paperclip_issue_id") or "") != issue_id:
                continue
            status = str(item.get("status") or "").upper()
            if status == target_status:
                return item
        time.sleep(max(0.5, poll_interval_s))
    raise TimeoutError(f"Timed out waiting for dispatch {issue_id} to reach {target_status}")


def _post_json(
    client: requests.Session,
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Optional[Mapping[str, str]] = None,
    auth: Optional[tuple[str, str]] = None,
) -> Dict[str, Any]:
    resp = client.post(url, data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False), headers=dict(headers or {}), auth=auth, timeout=15)
    resp.raise_for_status()
    if not resp.content:
        return {}
    return resp.json()


def _get_json(
    client: requests.Session,
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    auth: Optional[tuple[str, str]] = None,
) -> Dict[str, Any]:
    resp = client.get(url, params=params, auth=auth, timeout=15)
    resp.raise_for_status()
    if not resp.content:
        return {}
    return resp.json()


def _absolute_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _auth_tuple(user: Optional[str], password: Optional[str]) -> Optional[tuple[str, str]]:
    if user is None or password is None:
        return None
    return (user, password)
