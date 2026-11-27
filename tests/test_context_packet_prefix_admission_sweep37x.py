"""Tier-3 ContextPacket admission: a request prefix must not be satisfied by an
unlinked legacy packet (documented P1 Decision 5, never implemented on base).

Hermetic: pure functions, in-memory fixtures, explicit clock. No DB, no network.
"""

from __future__ import annotations

from copy import deepcopy

from assistx.allocation_engine import build_allocation_plan
from assistx.kv_cache import context_packet_match

PREFIX = "prefix-" + "a" * 64
OTHER_PREFIX = "prefix-" + "b" * 64
QUERY_HASH = "query-" + "c" * 64
OTHER_HASH = "query-" + "d" * 64
NOW_MS = 2_000


def _request(**overrides):
    request = {
        "prefix_id": PREFIX,
        "query_hash": QUERY_HASH,
        "privacy_scope": "project",
        "scope_id": "auto-assist",
    }
    request.update(overrides)
    return request


def _packet(**overrides):
    packet = {
        "id": "packet-1",
        "prefix_id": PREFIX,
        "privacy_scope": "project",
        "scope_id": "auto-assist",
        "status": "READY",
        "token_count": 20_000,
        "expires_at_ts": 9_999_999_999_999,
        "model_id": "model",
    }
    packet.update(overrides)
    return packet


def test_exact_prefix_match_still_wins():
    match = context_packet_match(_request(), [_packet()], now_ms=NOW_MS)
    assert match["mode"] == "context_packet"
    assert match["packet"]["id"] == "packet-1"


def test_legacy_packet_with_no_prefix_and_matching_hash_is_allowed():
    packet = _packet(id="legacy-linked", prefix_id=None, query_hash=QUERY_HASH)
    match = context_packet_match(_request(), [packet], now_ms=NOW_MS)
    assert match["mode"] == "context_packet"
    assert match["packet"]["id"] == "legacy-linked"


def test_legacy_packet_with_wrong_hash_is_rejected():
    """Fail-open on base: empty prefix bypasses the query_hash link entirely."""
    packet = _packet(id="legacy-unlinked", prefix_id=None, query_hash=OTHER_HASH)
    assert context_packet_match(_request(), [packet], now_ms=NOW_MS) == {
        "mode": "miss",
        "reason": "no_matching_context_packet",
    }


def test_legacy_packet_without_any_hash_is_rejected():
    packet = _packet(id="legacy-hashless", prefix_id=None)
    assert context_packet_match(_request(), [packet], now_ms=NOW_MS)["mode"] == "miss"


def test_packet_with_other_prefix_is_rejected_even_with_matching_hash():
    packet = _packet(id="other-prefix", prefix_id=OTHER_PREFIX, query_hash=QUERY_HASH)
    assert context_packet_match(_request(), [packet], now_ms=NOW_MS)["mode"] == "miss"


def test_request_without_prefix_uses_query_hash_only():
    request = _request(prefix_id=None)
    linked = _packet(id="linked", prefix_id=None, query_hash=QUERY_HASH)
    unlinked = _packet(id="unlinked", prefix_id=None, query_hash=None)
    assert context_packet_match(request, [linked], now_ms=NOW_MS)["packet"]["id"] == "linked"
    assert context_packet_match(request, [unlinked], now_ms=NOW_MS)["mode"] == "miss"


def test_matching_does_not_mutate_packet_catalog():
    packets = [_packet(), _packet(id="legacy", prefix_id=None, query_hash=OTHER_HASH)]
    before = deepcopy(packets)
    context_packet_match(_request(), packets, now_ms=NOW_MS)
    assert packets == before


def test_allocation_plan_does_not_credit_unlinked_packet():
    """An unlinked packet must not earn a tier-3 bonus for any placement."""
    request = {
        "prefix_id": PREFIX,
        "query_hash": OTHER_HASH,
        "privacy_scope": "project",
        "scope_id": "auto-assist",
    }
    task = {"id": "t", "status": "READY", "payload": {"kv_cache": request}}
    nodes = [
        {"hostname": "with-packet", "online": True, "loaded_models": ["model"]},
        {"hostname": "better-node", "online": True, "loaded_models": ["model"]},
    ]
    values = {
        "entries": [
            {
                "node_id": "with-packet",
                "model_id": "model",
                "quality_score": 0.8,
                "confidence": 0.8,
                "tokens_per_second": 50,
            },
            {
                "node_id": "better-node",
                "model_id": "model",
                "quality_score": 0.81,
                "confidence": 0.8,
                "tokens_per_second": 50,
            },
        ]
    }
    packet = _packet(id="unlinked", prefix_id=None, query_hash=QUERY_HASH)
    plan = build_allocation_plan([task], nodes, values, context_packets=[packet])
    recommendation = plan["recommendations"][0]
    assert recommendation["recommended"]["node_id"] == "better-node"
    assert recommendation["opportunity_cost"] == 0.0022
    for candidate in [recommendation["recommended"], *recommendation["alternatives"]]:
        assert candidate["components"]["context_packet_mode"] == "miss"
        assert candidate["components"]["context_packet_bonus"] == 0
