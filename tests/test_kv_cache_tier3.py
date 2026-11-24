"""Draft unit tests for KV-cache tier 3 (ContextPacket fallback)."""

from assistx.allocation_engine import build_allocation_plan
from assistx.kv_cache import (
    CONTEXT_PACKET_MAX_BONUS,
    cache_match,
    cache_value,
    context_packet_bonus,
    context_packet_match,
)


def _request():
    return {
        "prefix_id": "prefix-" + "a" * 64,
        "privacy_scope": "project",
        "scope_id": "auto-assist",
    }


def _packet(**overrides):
    packet = {
        "id": "packet-1",
        "prefix_id": _request()["prefix_id"],
        "privacy_scope": "project",
        "scope_id": "auto-assist",
        "status": "READY",
        "token_count": 20_000,
        "expires_at_ts": 9_999_999_999_999,
        "model_id": "model",
    }
    packet.update(overrides)
    return packet


def test_context_packet_bonus_is_bounded_and_below_kv_cache_cap():
    match = context_packet_match(_request(), [_packet()], now_ms=2_000)
    bonus = context_packet_bonus(match, tokens_per_second=100)

    assert match["mode"] == "context_packet"
    assert bonus["seconds_saved"] == 100  # 20k / 100 * 0.5
    assert 0 < bonus["score_bonus"] <= CONTEXT_PACKET_MAX_BONUS
    assert bonus["score_bonus"] < 0.18


def test_scope_expiry_and_status_are_enforced():
    request = _request()
    rejected = [
        _packet(id="wrong-scope", scope_id="other"),
        _packet(id="expired", expires_at_ts=1),
        _packet(id="not-ready", status="DRAFT"),
    ]
    assert context_packet_match(request, rejected, now_ms=2_000)["mode"] == "miss"
    assert context_packet_match(
        request, rejected + [_packet(id="good")], now_ms=2_000
    )["packet"]["id"] == "good"


def test_tier3_only_applies_when_tiers1_2_miss():
    request = _request()
    manifest = {
        "cache_id": "cache-1",
        "prefix_id": request["prefix_id"],
        "privacy_scope": "project",
        "scope_id": "auto-assist",
        "node_id": "cached",
        "model_id": "model",
        "status": "READY",
        "expires_at_ts": 9_999_999_999_999,
        "token_count": 20_000,
        "capabilities": {},
    }
    hit = cache_match(request, {"node_id": "cached", "model_id": "model"}, [manifest])
    assert hit["mode"] == "local"
    assert cache_value(hit, tokens_per_second=100)["score_bonus"] == 0.18
    assert context_packet_bonus({"mode": "miss"}, tokens_per_second=100)["score_bonus"] == 0


def test_allocation_plan_adds_tier3_component():
    request = _request()
    task = {"id": "t", "status": "READY", "payload": {"kv_cache": request}}
    nodes = [
        {"hostname": "with-packet", "online": True, "loaded_models": ["model"]},
        {"hostname": "no-packet", "online": True, "loaded_models": ["model"]},
    ]
    values = {"entries": [
        {"node_id": n, "model_id": "model", "quality_score": .8,
         "confidence": .8, "tokens_per_second": 50}
        for n in ("with-packet", "no-packet")
    ]}

    plan = build_allocation_plan(
        [task], nodes, values, context_packets=[_packet()]
    )
    recommended = plan["recommendations"][0]["recommended"]
    assert recommended["node_id"] == "with-packet"
    assert recommended["components"]["context_packet_mode"] == "context_packet"
    assert recommended["components"]["context_packet_bonus"] > 0
