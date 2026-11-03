"""Research-only DSH-inspired work intake gate for AssistX.

Pure synthetic metadata -> shadow decision. No body/text is accepted: content
never grants permissions. No task, shell command, network operation, cost,
notification, persistent event cursor, or inference dispatch is performed.
Upstream inspiration: deepseek-ai/dsh-superagent-prototype, docs/001-003.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

EVENT_FIELDS = frozenset({
    "source", "repository", "event_id", "source_sequence", "thread_id",
    "kind", "actor", "actor_kind", "actor_verified", "addressed",
    "policy_version",
})
POLICY_FIELDS = frozenset({
    "source", "repository", "version", "accept_mode",
    "allowed_humans", "allowed_bots",
})
STATE_FIELDS = frozenset({
    "policy_sha256", "last_source_sequence", "seen_event_ids",
    "owned_thread_ids", "source_binding_verified",
})
KINDS = frozenset({"issue_opened", "issue_comment", "pull_request_opened",
                  "pull_request_comment", "workflow_completed"})
ACTOR_KINDS = frozenset({"human", "bot", "ci"})
ID = re.compile(r"[A-Za-z0-9_.:\-/]{1,128}\Z")
ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-\[\]]{0,60}\Z")
POLICY_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_SEEN = 512
MAX_THREADS = 512

class IntakeRejected(ValueError):
    """Invalid, ambiguous, or unverifiable candidate metadata."""


def _record(payload: Any, required: frozenset, name: str) -> dict:
    if type(payload) is not dict or set(payload) != required:
        raise IntakeRejected(f"{name}: invalid or unexpected fields")
    return payload


def _id(value: Any, field: str) -> str:
    if type(value) is not str or ID.fullmatch(value) is None:
        raise IntakeRejected(f"{field}: invalid identifier")
    return value


def _actor(value: Any) -> str:
    if type(value) is not str or ACTOR.fullmatch(value) is None:
        raise IntakeRejected("actor: invalid identity")
    return value


def _string_set(value: Any, kind: str) -> set[str]:
    if type(value) is not list or len(value) > 64:
        raise IntakeRejected(f"{kind}: expected bounded list")
    items = [_actor(v) for v in value]
    if len(set(items)) != len(items):
        raise IntakeRejected(f"{kind}: duplicates")
    return set(items)


def fingerprint(policy: dict) -> str:
    _record(policy, POLICY_FIELDS, "policy")
    if policy["source"] != "github":
        raise IntakeRejected("only github policy source is modelled")
    repo = _id(policy["repository"], "repository")
    if (repo.count("/") != 1
            or any(part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", part)
                   for part in repo.split("/"))):
        raise IntakeRejected("repository must be a canonical owner/name")
    version = policy["version"]
    if type(version) is not str or POLICY_VERSION.fullmatch(version) is None:
        raise IntakeRejected("invalid policy version")
    if policy["accept_mode"] not in ("off", "shadow"):
        raise IntakeRejected("only off/shadow evaluation modes are allowed")
    _string_set(policy["allowed_humans"], "allowed_humans")
    _string_set(policy["allowed_bots"], "allowed_bots")
    blob = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(blob).hexdigest()


def evaluate(policy: dict, event: dict, state: dict) -> dict:
    """Returns bounded, non-executable audit metadata only.

    source_binding_verified and policy_sha256 are caller-supplied evidence
    fields; this function cannot independently authenticate either. Even
    a candidate outcome is NEVER a grant of execution authorization.
    """
    expected_policy = fingerprint(policy)
    _record(event, EVENT_FIELDS, "event")
    _record(state, STATE_FIELDS, "state")
    if event["source"] != "github" or event["repository"] != policy["repository"]:
        raise IntakeRejected("source binding does not match policy")
    event_id = _id(event["event_id"], "event_id")
    thread = _id(event["thread_id"], "thread_id")
    if type(event["kind"]) is not str or event["kind"] not in KINDS:
        raise IntakeRejected("unknown event kind")
    actor = _actor(event["actor"])
    if type(event["actor_kind"]) is not str or event["actor_kind"] not in ACTOR_KINDS:
        raise IntakeRejected("unknown actor classification")
    if type(event["actor_verified"]) is not bool or type(event["addressed"]) is not bool:
        raise IntakeRejected("actor verification/addressing must be boolean")
    if event["policy_version"] != policy["version"]:
        # Old policy authority cannot be silently promoted; caller must
        # revalidate under current policy in a separate authorized workflow.
        raise IntakeRejected("event policy version differs from current policy")
    seq = event["source_sequence"]
    if type(seq) is not int or seq < 0:
        raise IntakeRejected("invalid source sequence")
    prev = state["last_source_sequence"]
    if type(prev) is not int or prev < -1:
        raise IntakeRejected("invalid source watermark")
    seen = state["seen_event_ids"]
    owned = state["owned_thread_ids"]
    if type(seen) is not list or len(seen) > MAX_SEEN:
        raise IntakeRejected("invalid source event history")
    if type(owned) is not list or len(owned) > MAX_THREADS:
        raise IntakeRejected("invalid owned thread list")
    seen_ids = [_id(x, "seen_event_id") for x in seen]
    owned_ids = [_id(x, "owned_thread_id") for x in owned]
    if len(seen_ids) != len(set(seen_ids)) or len(owned_ids) != len(set(owned_ids)):
        raise IntakeRejected("duplicate snapshot identifiers")
    if type(state["source_binding_verified"]) is not bool:
        raise IntakeRejected("source binding flag is malformed")
    pin = state["policy_sha256"]
    if type(pin) is not str or SHA.fullmatch(pin) is None:
        raise IntakeRejected("policy pin must be SHA-256")
    reasons = []
    decision = "IGNORED"
    if pin != expected_policy:
        decision = "HOLD_POLICY_FINGERPRINT"
        reasons.append("policy snapshot differs from supplied pin")
    elif not state["source_binding_verified"] or not event["actor_verified"]:
        decision = "HOLD_UNVERIFIED_SOURCE_OR_ACTOR"
        reasons.append("external source identity evidence incomplete")
    elif event_id in seen_ids:
        decision = "DUPLICATE_IGNORED"
        reasons.append("event id already processed by trusted source")
    elif seq <= prev:
        decision = "HOLD_REPLAY_OR_OUT_OF_ORDER"
        reasons.append("source sequence not newer than checkpoint")
    elif thread in owned_ids:
        decision = "CONTEXT_ONLY"
        reasons.append("thread is already owned; no new mandate")
    elif policy["accept_mode"] == "off":
        decision = "IGNORED_POLICY_OFF"
        reasons.append("proactive intake disabled")
    elif event["actor_kind"] == "human" and actor in _string_set(policy["allowed_humans"], "allowed_humans"):
        if event["addressed"]:
            decision = "EXPLICIT_SHADOW_CANDIDATE"
            reasons.append("trusted allowed human addressed agent")
        else:
            decision = "INFERRED_SHADOW_CANDIDATE"
            reasons.append("allowed human, but no direct request")
    elif event["actor_kind"] == "bot" and actor in _string_set(policy["allowed_bots"], "allowed_bots"):
        decision = "INFERRED_SHADOW_CANDIDATE"
        reasons.append("allowed bot can only suggest work")
    else:
        decision = "IGNORED_UNAUTHORIZED_OR_PASSIVE"
        reasons.append("no trusted explicit work authorization")
    minimal = {
        "event_id": event_id,
        "thread_id": thread,
        "source_sequence": seq,
        "kind": event["kind"],
        "policy_fingerprint": expected_policy,
    }
    return {
        "kind": "dsh_inspired_work_shadow_v1",
        "candidate_evidence_sha256": hashlib.sha256(
            json.dumps(minimal, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "policy_sha256": expected_policy,
        "source": "github",
        "decision": decision,
        "reason_codes": reasons,
        "is_shadow_candidate": decision.endswith("SHADOW_CANDIDATE"),
        "current_policy_version": policy["version"],
        "policy_pin_matched": pin == expected_policy,
        "source_identity_independently_verified": False,
        "authenticated_mandate_verified": False,
        "source_cursor_persisted": False,
        "task_or_ticket_created": False,
        "existing_ticket_mutated": False,
        "proposed_provider_cost_usd": None,
        "model_tokens_consumed": 0,
        "fleet_dispatch_allowed": False,
        "agent_command_allowed": False,
        "public_comment_or_notification_allowed": False,
        "production_gate": "HOLD",
        "note": "Shadow observation only: an asserted verified flag cannot prove identity, policy custody, or an actual human mandate.",
    }
