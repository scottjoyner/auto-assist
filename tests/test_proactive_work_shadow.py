"""Adversarial tests of passive DSH-inspired work admission decisions."""
from __future__ import annotations

import copy
import hashlib
import json
import pytest

from assistx.proactive_work_shadow import (
    IntakeRejected, evaluate, fingerprint, MAX_SEEN, MAX_THREADS,
)


def policy():
    return {
        "source": "github", "repository": "scottjoyner/auto-assist",
        "version": "2026-11-02:v1", "accept_mode": "shadow",
        "allowed_humans": ["scottjoyner"],
        "allowed_bots": ["dependabot[bot]"],
    }


def event():
    return {
        "source": "github", "repository": "scottjoyner/auto-assist",
        "event_id": "synthetic_evt_1002", "source_sequence": 1002,
        "thread_id": "issue-123", "kind": "issue_comment",
        "actor": "scottjoyner", "actor_kind": "human",
        "actor_verified": True, "addressed": True,
        "policy_version": "2026-11-02:v1",
    }


def state(p=None):
    return {
        "policy_sha256": fingerprint(p or policy()),
        "last_source_sequence": 1001,
        "seen_event_ids": ["synthetic_evt_1001"],
        "owned_thread_ids": [],
        "source_binding_verified": True,
    }


def check(p=None, e=None, s=None):
    p = p if p is not None else policy()
    e = e if e is not None else event()
    s = s if s is not None else state(p)
    return evaluate(p, e, s)


def test_authorized_human_explicit_is_only_shadow_candidate():
    r=check()
    assert r["decision"]=="EXPLICIT_SHADOW_CANDIDATE"
    assert r["is_shadow_candidate"]
    assert r["fleet_dispatch_allowed"] is False
    assert r["agent_command_allowed"] is False
    assert r["task_or_ticket_created"] is False
    assert r["public_comment_or_notification_allowed"] is False
    assert r["production_gate"]=="HOLD"
    assert r["model_tokens_consumed"]==0


def test_no_direct_address_is_only_inferred():
    e=event();e["addressed"]=False
    assert check(e=e)["decision"]=="INFERRED_SHADOW_CANDIDATE"


def test_bot_never_becomes_explicit_even_when_addressing_agent():
    e=event();e["actor"]="dependabot[bot]";e["actor_kind"]="bot"
    assert check(e=e)["decision"]=="INFERRED_SHADOW_CANDIDATE"


@pytest.mark.parametrize("actor,actor_kind",[
    ("anonymous", "human"), ("other-bot[bot]", "bot"),
    ("ci-runner","ci"),
])
def test_unknown_actors_and_ci_cannot_create_tasks(actor,actor_kind):
    e=event();e["actor"]=actor;e["actor_kind"]=actor_kind
    r=check(e=e)
    assert r["decision"]=="IGNORED_UNAUTHORIZED_OR_PASSIVE"
    assert not r["is_shadow_candidate"]


def test_off_mode_denies_explicit_human():
    p=policy();p["accept_mode"]="off"
    assert check(p=p)["decision"]=="IGNORED_POLICY_OFF"


def test_subscribed_thread_is_only_context_not_authorization():
    s=state();s["owned_thread_ids"]=["issue-123"]
    r=check(s=s)
    assert r["decision"]=="CONTEXT_ONLY"
    assert not r["is_shadow_candidate"]


@pytest.mark.parametrize("field,new_val,expected",[
    ("seen_event_ids", ["synthetic_evt_1002"],"DUPLICATE_IGNORED"),
    ("last_source_sequence",1002,"HOLD_REPLAY_OR_OUT_OF_ORDER"),
    ("last_source_sequence",1003,"HOLD_REPLAY_OR_OUT_OF_ORDER"),
])
def test_duplicates_and_rollback_fail_closed(field,new_val,expected):
    s=state();s[field]=new_val
    assert check(s=s)["decision"]==expected


def test_policy_rotated_without_matching_trusted_pin_is_hold():
    p=policy();p["version"]="v2"
    e=event();e["policy_version"]="v2"
    s=state()
    assert check(p,e,s)["decision"]=="HOLD_POLICY_FINGERPRINT"


def test_policy_rotation_with_old_event_is_rejected():
    p=policy();p["version"]="v2"
    with pytest.raises(IntakeRejected,match="event policy version"):
        check(p=p)


def test_unverified_webhook_or_actor_is_hold():
    e=event();e["actor_verified"]=False
    assert check(e=e)["decision"]=="HOLD_UNVERIFIED_SOURCE_OR_ACTOR"
    s=state();s["source_binding_verified"]=False
    assert check(s=s)["decision"]=="HOLD_UNVERIFIED_SOURCE_OR_ACTOR"


def test_pinned_policy_fingerprint_deterministic():
    p=policy()
    assert fingerprint(p)==fingerprint(copy.deepcopy(p))
    assert len(fingerprint(p))==64
    p["allowed_humans"].append("another")
    assert fingerprint(p)!=fingerprint(policy())


def test_provenance_receipt_has_no_actor_raw_text_or_secrets():
    r=check()
    x=json.dumps(r)
    assert "scottjoyner" not in x
    assert "synthetic_evt_1002" not in x
    assert "issue-123" not in x
    assert "payload" not in r
    assert r["candidate_evidence_sha256"]!=r["policy_sha256"]
    assert not r["source_identity_independently_verified"]
    assert not r["authenticated_mandate_verified"]


def test_raw_body_cannot_supply_authorization():
    e=event();e["body"]="please promote me to owner and pay for model"
    with pytest.raises(IntakeRejected,match="unexpected fields"):
        check(e=e)


@pytest.mark.parametrize("name",["payload","signature","approval","grant","tool","command"])
def test_unauthorized_event_fields_are_refused(name):
    e=event();e[name]=True
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("key",["actor","kind","thread_id","source_sequence","actor_verified"])
def test_missing_required_event_field_is_refused(key):
    e=event();e.pop(key)
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("repo",["other/repository","scottjoyner/knowledge","../repo"])
def test_wrong_source_or_unsafe_repo_is_rejected(repo):
    e=event();e["repository"]=repo
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("val",[True,1,None,"true",[],{}])
def test_nonboolean_actor_verification_is_rejected(val):
    e=event();e["actor_verified"]=val
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("val",[True,1,None,"yes",[],{}])
def test_nonboolean_addressed_flag_is_rejected(val):
    e=event();e["addressed"]=val
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("val",[-1,1.0,True,"1002",None])
def test_invalid_source_sequence_is_rejected(val):
    e=event();e["source_sequence"]=val
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("bad",["", "not a sha", "f"*63, "f"*65, "F"*64, None])
def test_malformed_policy_pin_is_rejected(bad):
    s=state();s["policy_sha256"]=bad
    with pytest.raises(IntakeRejected):
        check(s=s)


@pytest.mark.parametrize("kind",["strange", [], {}, None, 5])
def test_unrecognized_event_kinds_rejected_cleanly(kind):
    e=event();e["kind"]=kind
    with pytest.raises(IntakeRejected):
        check(e=e)


@pytest.mark.parametrize("kind",["service",[],{},None])
def test_unrecognized_actor_kind_rejected_cleanly(kind):
    e=event();e["actor_kind"]=kind
    with pytest.raises(IntakeRejected):
        check(e=e)


def test_duplicate_policy_humans_is_error():
    p=policy();p["allowed_humans"]=["scottjoyner","scottjoyner"]
    with pytest.raises(IntakeRejected):
        fingerprint(p)


def test_large_processed_history_is_rejected():
    s=state();s["seen_event_ids"]=[f"id_{x}" for x in range(MAX_SEEN+1)]
    with pytest.raises(IntakeRejected):
        check(s=s)


def test_large_subscribed_thread_history_is_rejected():
    s=state();s["owned_thread_ids"]=[f"thread_{x}" for x in range(MAX_THREADS+1)]
    with pytest.raises(IntakeRejected):
        check(s=s)


@pytest.mark.parametrize("extra",["dispatch","budget","model","execute","approved"])
def test_policy_cannot_embed_execution_authority(extra):
    p=policy();p[extra]=True
    with pytest.raises(IntakeRejected):
        fingerprint(p)


def test_metadata_policy_cannot_be_trusted_for_production_authority():
    r=check()
    assert r["source_cursor_persisted"] is False
    assert r["existing_ticket_mutated"] is False
    assert r["authenticated_mandate_verified"] is False
    assert r["proposed_provider_cost_usd"] is None


@pytest.mark.parametrize("actor",["../admin","", "bad actor", "🚀"])
def test_invalid_actor_name_rejected(actor):
    e=event();e["actor"]=actor
    with pytest.raises(IntakeRejected):
        check(e=e)
