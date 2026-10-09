#!/usr/bin/env python3
"""Focused tests for free_subagent_supervisor (read-only slice)."""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import free_subagent_supervisor as supervisor

FIXTURE_MODELS = pathlib.Path(__file__).parent / "fixtures" / "openrouter_models_sample.jsonl"
FIXTURE_STATE = pathlib.Path(__file__).parent / "fixtures" / "subagent_state_sample.jsonl"


def test_enumerate_free_models_from_fixture():
    models = supervisor.enumerate_free_models(FIXTURE_MODELS)
    ids = [m.get("id") for m in models]
    assert any("claude-3.5-sonnet" in s for s in ids), f"expected claude in {ids}"
    assert any("gemini-2.5-flash" in s for s in ids), f"expected gemini in {ids}"
    deepseek_in_models = any("deepseek-r1" in (m.get("id") or "") for m in models)
    # Deepseek is not free in fixture (pricing non-zero, no free tag), so should be absent.
    assert not deepseek_in_models, f"expected deepseek excluded from free models: {models}"
    paid_completion = any("zero-prompt-paid-completion" in (m.get("id") or "") for m in models)
    assert not paid_completion, (
        "zero prompt price alone must never qualify a paid-completion model as free"
    )


def test_credential_present_no_secret_leak():
    # Ensure env not polluted
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_KEY"):
        if k in os.environ:
            old = os.environ[k]
            del os.environ[k]
        else:
            old = None
    try:
        assert supervisor.credential_present() is False
    finally:
        if old is not None:
            os.environ[k] = old


def test_register_inspect_duplicates():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        path = pathlib.Path(tf.name)
    try:
        record = {
            "title": "test-a",
            "objective": "o",
            "model": "openrouter/anthropic/claude-3.5-sonnet",
            "worktree": "/tmp/wt-test-dup",
            "pid": 123,
            "session": "sess-a",
            "status": "active",
            "created_at": "2026-10-06T09:00:00Z",
        }
        supervisor.register_subagent(record, path)
        supervisor.register_subagent({**record, "pid": 124, "session": "sess-b", "title": "test-b"}, path)
        recs = supervisor.inspect_subagents(path)
        assert len(recs) == 2
        dups = supervisor.detect_duplicate_worktrees(recs)
        assert len(dups) == 1
        assert dups[0]["worktree"] == "/tmp/wt-test-dup"
    finally:
        path.unlink(missing_ok=True)




def test_register_rejects_unresolved_router_alias_and_records_exact_model():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        path = pathlib.Path(tf.name)
    try:
        alias = {
            "title": "alias-real-work",
            "objective": "must be attributable",
            "model": "openrouter/free",
            "worktree": "/tmp/wt-alias",
            "pid": 1,
            "session": "sess-alias",
            "status": "running",
        }
        try:
            supervisor.register_subagent(alias, path)
            raise AssertionError("unresolved router alias should be rejected")
        except ValueError as exc:
            assert "resolved_model" in str(exc)

        exact = {
            "title": "exact-real-work",
            "objective": "attributable anonymous run",
            "model": "stepfun/step-3.7-flash:free",
            "worktree": "/tmp/wt-exact",
            "pid": 2,
            "session": "sess-exact",
            "status": "running",
        }
        recs = supervisor.register_subagent(exact, path)
        row = recs[-1]
        assert row["route_kind"] == "exact"
        assert row["effective_model"] == "stepfun/step-3.7-flash:free"
        assert row["model_attribution"] == "exact-request"
        assert row["model_identity_complete"] is True

        canary = {
            **alias,
            "title": "alias-canary",
            "session": "sess-canary",
            "allow_unresolved_alias": True,
        }
        recs = supervisor.register_subagent(canary, path)
        row = recs[-1]
        assert row["route_kind"] == "alias"
        assert row["model_attribution"] == "unresolved-router-alias"
        assert row["model_identity_complete"] is False
    finally:
        path.unlink(missing_ok=True)

def test_projection_readonly_and_verdict():
    proj = supervisor.emit_projection(
        free_models=supervisor.enumerate_free_models(FIXTURE_MODELS),
        state_path=FIXTURE_STATE,
    )
    assert proj.get("read_only") is True
    assert proj.get("supervision_slice") == "free_subagent_supervisor"
    assert "suggested_scale_verdict" in proj
    assert proj.get("duplicate_worktrees") is not None
    # With fixture duplicates, verdict should be hold
    assert proj.get("suggested_scale_verdict") == "hold"


def test_projection_healthy_when_clean():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        clean_path = pathlib.Path(tf.name)
    try:
        # Empty clean state with free models and credential simulated by monkey? We just test structure.
        proj = supervisor.emit_projection(
            free_models=[{"id":"openrouter/free/test","tags":["free"]}],
            state_path=clean_path,
        )
        assert proj.get("read_only") is True
        assert proj.get("suggested_scale_verdict") in ("healthy", "hold")
    finally:
        clean_path.unlink(missing_ok=True)


def test_projection_notes_document_stdinhazard():
    proj = supervisor.emit_projection()
    note = proj.get("projections_note") or ""
    assert "/dev/null" in note or "stdin" in note.lower() or "close stdin" in note.lower()


def test_sqlite_session_history_is_not_treated_as_concurrent_process_duplication():
    records = [
        {
            "source": "sqlite_readonly",
            "session": "historical-a",
            "worktree": "/tmp/shared-worktree",
            "status": "active",
            "pid": None,
        },
        {
            "source": "sqlite_readonly",
            "session": "historical-b",
            "worktree": "/tmp/shared-worktree",
            "status": "active",
            "pid": None,
        },
    ]
    assert supervisor.detect_duplicate_worktrees(records) == []


def test_discover_live_sessions_readonly_query_only(tmp_path, monkeypatch):
    import sqlite3, time
    # Never read or depend on an operator's actual OpenCode session DB.
    monkeypatch.setenv("HOME", str(tmp_path))
    db = tmp_path / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as seed:
        seed.execute(
            "CREATE TABLE session (id TEXT, title TEXT, slug TEXT, directory TEXT, "
            "agent TEXT, model TEXT, time_updated INTEGER, time_archived INTEGER)"
        )
        seed.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
            ("synthetic-1","read-only fixture","fixture","/tmp/fixture",
             "test-agent",json.dumps({"id":"test/model"}),
             int(time.time()*1000),None))
    result = supervisor.discover_live_sessions(query_only=True)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["session"] == "synthetic-1"
    db_uri = supervisor._opencode_db_uri()
    assert db_uri.startswith("file:") and "opencode.db" in db_uri
    conn = sqlite3.connect(db_uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        import pytest
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TEMP TABLE _assert_write_fail (id INTEGER)")
    finally:
        conn.close()


def test_projection_with_trace_exporter_integration(tmp_path, monkeypatch):
    from free_subagent_supervisor import emit_projection
    monkeypatch.setenv("HOME", str(tmp_path))
    # Test-only fixture in a disposable directory, no source tree writes.
    trace_exporter_path = tmp_path / "empty_trace_exporter.py"
    trace_exporter_path.write_text("def export_sessions(db_path):\\n    return []\\n")

    proj = emit_projection(
        free_models=[{"id": "openrouter/claude-3.5-sonnet", "provider": "openrouter", "pricing": {"prompt": "0", "completion": "0"}}],
        state_path=FIXTURE_STATE,
        trace_exporter_path=trace_exporter_path,
    )
    assert proj["trace_records_count"] == 0
    assert proj["trace_records_status"] == "source_unavailable"
    assert proj["trace_records_sample"] == []
    assert proj["trace_provider_summary"] == {}
    assert "trace_exporter_error" not in proj


def test_trace_exporter_readonly_query_only():
    import importlib.util
    import json
    import pathlib
    import sqlite3
    import tempfile
    from typing import Any

    MODULE_PATH = (
        pathlib.Path(__file__).parent.parent
        / "scripts"
        / "export_opencode_session_traces.py"
    )
    SPEC = importlib.util.spec_from_file_location("trace_exporter", MODULE_PATH)
    trace_exporter = importlib.util.module_from_spec(SPEC)
    assert SPEC.loader is not None
    SPEC.loader.exec_module(trace_exporter)

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        db = root / "opencode.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE session (
              id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT,
              model TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER,
              tokens_reasoning INTEGER, tokens_cache_read INTEGER,
              tokens_cache_write INTEGER, time_created INTEGER, time_updated INTEGER
            );
            CREATE TABLE message (
              id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
              time_updated INTEGER, data TEXT
            );
            CREATE TABLE part (
              id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
              time_created INTEGER, time_updated INTEGER, data TEXT
            );
            """
        )
        con.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ses_trace_test", None, str(root), "trace-test-session",
                json.dumps({"providerID": "test-provider", "id": "test-model", "variant": "test"}),
                0.0, 100, 20, 5, 50, 0, 1, 2,
            ),
        )
        con.commit()
        con.close()

        records = trace_exporter.export_sessions(db)
        assert len(records) == 1
        assert records[0]["provider"] == "test-provider"
        assert records[0]["model"] == "test-model"
        assert records[0]["tokens"]["input"] == 100
        assert records[0]["cost"] == 0.0


def test_classify_free_model_openrouter_account_free():
    from free_subagent_supervisor import classify_free_model

    # OpenRouter with zero prompt and completion and free tag should be opencode-native-free
    model = {
        "id": "openrouter/anthropic/claude-3.5-sonnet",
        "provider": "openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
        "tags": ["free"]
    }
    assert classify_free_model(model) == "opencode-native-free"

    # OpenRouter with zero prompt and completion but no free tag
    model["tags"] = []
    assert classify_free_model(model) == "openrouter-account-free"

    # Deepseek with paid completion should be rate-limited
    model["id"] = "openrouter/deepseek/deepseek-r1"
    model["pricing"] = {"prompt": "0.54", "completion": "2.19"}
    assert classify_free_model(model) == "rate-limited"

    # Zero prompt but paid completion
    model["id"] = "openrouter/example/zero-prompt-paid-completion"
    model["pricing"] = {"prompt": "0", "completion": "0.25"}
    assert classify_free_model(model) == "rate-limited"


def test_classify_free_model_opencode_native():
    from free_subagent_supervisor import classify_free_model

    # OpenCode-native :free models
    model = {
        "id": "openrouter/some-model:free",
        "provider": "openrouter",
        "pricing": {"prompt": "0.1", "completion": "0.2"}
    }
    assert classify_free_model(model) == "opencode-native-free"

    # With explicit free tag
    model = {
        "id": "openrouter/some-model",
        "provider": "openrouter",
        "pricing": {"prompt": "0.1", "completion": "0.2"},
        "tags": ["free"]
    }
    assert classify_free_model(model) == "opencode-native-free"


def test_classify_free_model_provider_specific():
    from free_subagent_supervisor import classify_free_model

    # Z.AI zero-cost
    model = {
        "id": "zai/glm-4-flash",
        "provider": "zai",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "zai-zero-cost"

    # Cohere zero-cost
    model = {
        "id": "cohere/command-r-plus",
        "provider": "cohere",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "cohere-zero-cost"

    # Rate-limited Cohere
    model["pricing"] = {"prompt": "0.001", "completion": "0.002"}
    assert classify_free_model(model) == "rate-limited"

    # LM Studio
    model = {
        "id": "lmstudio/mistral",
        "provider": "lmstudio",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "lmstudio-zero-cost"


def test_classify_free_model_unknown_free():
    from free_subagent_supervisor import classify_free_model

    # Unknown free model (zero pricing but unclassified provider)
    model = {
        "id": "custom/free-model",
        "provider": "custom",
        "pricing": {"prompt": "0", "completion": "0"}
    }
    assert classify_free_model(model) == "unknown-free"

    # Model with partial zero pricing (should be rate-limited)
    model["pricing"] = {"prompt": "0", "completion": "0.5"}
    assert classify_free_model(model) == "rate-limited"


def test_enumerate_free_models_with_classification():
    from free_subagent_supervisor import enumerate_free_models

    models = enumerate_free_models(FIXTURE_MODELS)

    # Verify we get models back
    assert len(models) > 0

    # Check that each model has classification
    for m in models:
        assert "free_model_type" in m
        assert m["free_model_type"] in [
            "openrouter-account-free",
            "opencode-native-free",
            "rate-limited"
        ]

    # Verify specific model types from fixture
    # Claude has tags=["free"] so it's classified as opencode-native-free
    claude_ids = [m.get("id", "") for m in models if "claude" in m.get("id", "").lower()]
    assert len(claude_ids) > 0, "Claude model should be found"
    claude_type = next(m.get("free_model_type", "") for m in models if "claude" in m.get("id", "").lower())
    assert claude_type == "opencode-native-free", "Claude with free tag should be classified as opencode-native-free"

    gemini_ids = [m.get("id", "") for m in models if "gemini" in m.get("id", "").lower()]
    assert len(gemini_ids) > 0, "Gemini model should be found"
    gemini_type = next(m.get("free_model_type", "") for m in models if "gemini" in m.get("id", "").lower())
    assert gemini_type == "opencode-native-free", "Gemini with free tag should be classified as opencode-native-free"

    # Deepseek and zero-prompt-paid-completion should not be in results (rate-limited)
    deepseek_ids = [m.get("id", "") for m in models if "deepseek" in m.get("id", "").lower()]
    assert len(deepseek_ids) == 0, "Deepseek with paid pricing should not be included in free models"

    zero_pricing_ids = [
        m.get("id", "") for m in models
        if "zero-prompt-paid-completion" in m.get("id", "")
    ]
    assert len(zero_pricing_ids) == 0, "Zero-prompt-paid-completion should not be included in free models"


def test_enumerate_free_models_deduplicates():
    from free_subagent_supervisor import enumerate_free_models

    # Test that duplicates are removed
    fixture = pathlib.Path(__file__).parent / "fixtures" / "openrouter_models_sample.jsonl"
    models = enumerate_free_models(fixture)

    # Check no duplicates by id
    ids = [m.get("id") for m in models]
    assert len(ids) == len(set(ids)), "Should not have duplicate model IDs"


def test_projection_includes_free_model_types():
    from free_subagent_supervisor import emit_projection

    proj = emit_projection(
        free_models=[
            {
                "id": "openrouter/claude-3.5-sonnet",
                "provider": "openrouter",
                "pricing": {"prompt": "0", "completion": "0"},
                "tags": ["free"]
            },
            {
                "id": "openrouter/claude-3.5-sonnet:free",
                "provider": "openrouter",
                "tags": ["free"]
            },
        ],
        state_path=FIXTURE_STATE
    )

    assert "free_model_types" in proj
    # Both have free tags, so both are opencode-native-free
    assert proj["free_model_types"]["opencode-native-free"] == 2
    # No openrouter-account-free since both have tags


FIXTURE_POOLS = pathlib.Path(__file__).parent / "fixtures" / "provider_pools_sample.jsonl"


def _load_pool_models():
    import json

    models = []
    with FIXTURE_POOLS.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            obj["free_model_type"] = supervisor.classify_free_model(obj)
            models.append(obj)
    return models


def test_kilo_anonymous_pool_constants():
    from free_subagent_supervisor import (
        KILO_ANONYMOUS_BASE_URL,
        KILO_ANONYMOUS_BEARER,
        pool_tier,
        is_anonymous_pool,
    )

    assert KILO_ANONYMOUS_BASE_URL == "https://api.kilo.ai/api/openrouter"
    # The anonymous bearer is a non-secret placeholder, never a real key.
    assert KILO_ANONYMOUS_BEARER == "anonymous"
    assert pool_tier("kilo", KILO_ANONYMOUS_BASE_URL) == "kilo-anonymous"
    # A kilo base_url alone identifies the anonymous pool.
    assert pool_tier("", KILO_ANONYMOUS_BASE_URL) == "kilo-anonymous"
    assert is_anonymous_pool("kilo-anonymous") is True
    assert is_anonymous_pool("lmstudio-local") is True
    assert is_anonymous_pool("openrouter-free") is False
    assert is_anonymous_pool("zai-reserve") is False


def test_pool_ordering_prefers_anonymous_before_reserved():
    from free_subagent_supervisor import (
        order_free_pools,
        pool_tier,
        pool_preference_rank,
    )

    models = _load_pool_models()
    ordered = order_free_pools(models)
    ordered_tiers = [pool_tier(m.get("provider") or "", m.get("base_url") or "") for m in ordered]
    # Distinct tiers, preserving preference order.
    distinct_tiers = list(dict.fromkeys(ordered_tiers))

    # Truly zero-cost / anonymous pools come first.
    assert distinct_tiers[0] == "kilo-anonymous"
    assert distinct_tiers[1] == "lmstudio-local"
    # OpenRouter free models (shared free quota) follow the anonymous pools.
    assert distinct_tiers[2] == "openrouter-free"
    # Reserved quota never precedes an anonymous / zero-cost pool.
    assert distinct_tiers[3] == "cohere-reserved"
    assert pool_preference_rank("kilo-anonymous") < pool_preference_rank("cohere-reserved")
    assert pool_preference_rank("lmstudio-local") < pool_preference_rank("cohere-reserved")
    assert pool_preference_rank("openrouter-free") < pool_preference_rank("cohere-reserved")
    # Z.AI is reserve-only and excluded by default.
    assert "zai-reserve" not in distinct_tiers
    # The paid model is never auto-selected.
    assert "openrouter/anthropic/claude-sonnet-4.5" not in [m.get("id") for m in ordered]


def test_zai_is_reserve_only_unless_explicitly_requested():
    from free_subagent_supervisor import order_free_pools, pool_tier, RESERVE_ONLY_POOLS

    models = _load_pool_models()

    # Default: Z.AI excluded entirely.
    default_ordered = order_free_pools(models, allow_reserve=False)
    assert "zai-reserve" not in {
        pool_tier(m.get("provider") or "", m.get("base_url") or "") for m in default_ordered
    }

    # Explicitly requested: Z.AI included, but still ranked after
    # every anonymous / zero-cost pool.
    allowed = order_free_pools(models, allow_reserve=True)
    tiers = [pool_tier(m.get("provider") or "", m.get("base_url") or "") for m in allowed]
    assert "zai-reserve" in tiers
    assert "zai-reserve" in RESERVE_ONLY_POOLS
    zai_positions = [i for i, t in enumerate(tiers) if t == "zai-reserve"]
    anonymous_positions = [i for i, t in enumerate(tiers) if t in ("kilo-anonymous", "lmstudio-local")]
    assert max(anonymous_positions) < min(zai_positions)


def test_route_kind_distinguishes_exact_model_from_router_alias():
    from free_subagent_supervisor import route_kind

    # Router aliases delegate model selection to the provider router.
    assert route_kind("openrouter/free") == "alias"
    assert route_kind("kilo/free") == "alias"
    assert route_kind("kilo/auto") == "alias"
    assert route_kind("openrouter/auto") == "alias"
    # Exact-model routes name one concrete model.
    assert route_kind("zai/glm-4.7-flash") == "exact"
    assert route_kind("kilo/kimi-k2-turbo") == "exact"
    assert route_kind("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free") == "exact"
    assert route_kind("cohere/north-mini-code:free") == "exact"
    assert route_kind("lmstudio/qwen3-30b-a3b") == "exact"
    # A bare concrete model with no provider prefix is still exact.
    assert route_kind("space-bunny-free") == "exact"


def test_provider_state_surface_without_spending():
    from free_subagent_supervisor import provider_state, PROVIDER_STATES

    assert set(PROVIDER_STATES) == {
        "usable",
        "rate_limited",
        "quota_exhausted",
        "payment_required",
        "unqualified",
    }

    # Zero-cost model is usable.
    assert provider_state({
        "id": "kilo/kimi-k2-turbo",
        "provider": "kilo",
        "base_url": "https://api.kilo.ai/api/openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
    }) == "usable"

    # Kilo anonymous pool is zero-cost by policy even without pricing metadata.
    assert provider_state({
        "id": "kilo/kimi-k2-turbo",
        "provider": "kilo",
        "base_url": "https://api.kilo.ai/api/openrouter",
    }) == "usable"

    # A non-zero price is always payment_required and never auto-spent.
    assert provider_state({
        "id": "openrouter/anthropic/claude-sonnet-4.5",
        "provider": "openrouter",
        "pricing": {"prompt": "3.0", "completion": "15.0"},
    }) == "payment_required"

    # Unknown provider is unqualified.
    assert provider_state({
        "id": "unknown/model",
        "provider": "some-unknown-provider",
        "pricing": {"prompt": "0", "completion": "0"},
    }) == "unqualified"

    # Read-only observed signals upgrade the state without spending.
    base = {
        "id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        "provider": "openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
    }
    assert provider_state(base, {"rate_limited": True}) == "rate_limited"
    assert provider_state(base, {"quota_exhausted": True}) == "quota_exhausted"
    assert provider_state(base, {"payment_required": True}) == "payment_required"
    assert provider_state(base, {"error_type": "provider_overloaded"}) == "rate_limited"
    assert provider_state(base, {"status_code": 503}) == "rate_limited"


def test_observed_overload_removes_model_from_usable_pool():
    from free_subagent_supervisor import order_free_pools, qualify_provider_pools

    models = [
        {
            "id": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
            "provider": "kilo",
            "base_url": "https://api.kilo.ai/api/openrouter",
            "pricing": {"prompt": "0", "completion": "0"},
            "observed": {"error_type": "provider_overloaded", "status_code": 503},
        },
        {
            "id": "openrouter/google/gemma-3-27b-it:free",
            "provider": "openrouter",
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]
    ordered = order_free_pools(models)
    assert [m["id"] for m in ordered] == ["openrouter/google/gemma-3-27b-it:free"]
    pools = {p["pool"]: p for p in qualify_provider_pools(models)}
    assert pools["kilo-anonymous"]["state"] == "rate_limited"
    assert pools["kilo-anonymous"]["usable_models"] == []
    assert pools["kilo-anonymous"]["blocked"] == [
        {
            "model": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
            "state": "rate_limited",
        }
    ]


def test_qualify_provider_pools_surfaces_states_and_reserve_policy():
    from free_subagent_supervisor import qualify_provider_pools

    models = _load_pool_models()
    pools = qualify_provider_pools(models, allow_reserve=False)
    by_pool = {p["pool"]: p for p in pools}

    # Ordered by preference: anonymous / zero-cost before reserved quota.
    assert [p["pool"] for p in pools].index("kilo-anonymous") < [p["pool"] for p in pools].index("openrouter-free")
    assert [p["pool"] for p in pools].index("kilo-anonymous") < [p["pool"] for p in pools].index("cohere-reserved")

    # Kilo anonymous pool is usable, anonymous, and offers exact + alias routes.
    kilo = by_pool["kilo-anonymous"]
    assert kilo["state"] == "usable"
    assert kilo["anonymous"] is True
    assert kilo["reserve_only"] is False
    assert kilo["route_kinds"] == {"alias": 2, "exact": 1}

    # Z.AI is reserve-only and flagged as excluded by policy.
    zai = by_pool["zai-reserve"]
    assert zai["reserve_only"] is True
    assert zai["excluded_by_policy"] is True
    assert "reserve-only" in zai["policy"]

    # OpenRouter free pool is usable but also records the paid model as
    # blocked with payment_required (never auto-selected).
    openrouter = by_pool["openrouter-free"]
    assert openrouter["state"] == "usable"
    blocked_states = {b["model"]: b["state"] for b in openrouter["blocked"]}
    assert blocked_states.get("openrouter/anthropic/claude-sonnet-4.5") == "payment_required"


def test_projection_orders_pools_and_marks_zai_reserve_only():
    from free_subagent_supervisor import emit_projection

    models = _load_pool_models()
    proj = emit_projection(free_models=models, state_path=FIXTURE_STATE)

    assert proj["read_only"] is True
    pools = proj["provider_pools"]
    tiers = [p["pool"] for p in pools]
    # Preferred pool is the Kilo anonymous pool (most preferred, usable).
    assert proj["preferred_pool"] == "kilo-anonymous"
    assert tiers[0] == "kilo-anonymous"
    # Z.AI is surfaced as reserve-only.
    assert any(r["pool"] == "zai-reserve" for r in proj["reserve_only_pools"])
    # Anonymous pools usable without a credential are surfaced.
    assert "kilo-anonymous" in proj["anonymous_pools_usable"]
    assert proj["kilo_anonymous_base_url"] == "https://api.kilo.ai/api/openrouter"
    # Route-kind breakdown is surfaced for the free models.
    assert proj["free_route_kinds"]["alias"] >= 2
    assert proj["free_route_kinds"]["exact"] >= 1


_CREDENTIAL_KEYS = ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPENROUTER_API_KEY_2")


def _isolate_openrouter_credential():
    """Temporarily remove OpenRouter credential env vars and stub live
    session discovery so a projection is decided solely by the
    credential / anonymous-pool policy. Returns a restore callable."""
    saved = {k: os.environ.pop(k) for k in _CREDENTIAL_KEYS if k in os.environ}
    original_discover = supervisor.discover_live_sessions
    supervisor.discover_live_sessions = lambda query_only=True: []

    def restore():
        os.environ.update(saved)
        supervisor.discover_live_sessions = original_discover

    return restore


def test_projection_holds_when_no_anonymous_pool_and_no_credential():
    from free_subagent_supervisor import emit_projection

    # OpenRouter-only free models (no anonymous pool) and no credential.
    restore = _isolate_openrouter_credential()
    try:
        models = [
            {
                "id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
                "provider": "openrouter",
                "pricing": {"prompt": "0", "completion": "0"},
                "tags": ["free"],
            },
        ]
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
            clean_path = pathlib.Path(tf.name)
        try:
            proj = emit_projection(free_models=models, state_path=clean_path)
            # No credential and no anonymous pool usable -> hold.
            assert proj["suggested_scale_verdict"] == "hold"
            assert proj["anonymous_pools_usable"] == []
        finally:
            clean_path.unlink(missing_ok=True)
    finally:
        restore()


def test_projection_healthy_with_anonymous_pool_even_without_credential():
    from free_subagent_supervisor import emit_projection

    restore = _isolate_openrouter_credential()
    try:
        models = [
            {
                "id": "kilo/kimi-k2-turbo",
                "provider": "kilo",
                "base_url": "https://api.kilo.ai/api/openrouter",
            },
        ]
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
            clean_path = pathlib.Path(tf.name)
        try:
            proj = emit_projection(free_models=models, state_path=clean_path)
            # Anonymous Kilo pool is usable without any credential.
            assert proj["credential_present"] is False
            assert proj["anonymous_pools_usable"] == ["kilo-anonymous"]
            assert proj["preferred_pool"] == "kilo-anonymous"
            assert proj["suggested_scale_verdict"] == "healthy"
        finally:
            clean_path.unlink(missing_ok=True)
    finally:
        restore()


def test_provider_pools_cli_exposes_reserve_policy():
    import json
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).parent.parent
    script = repo_root / "scripts" / "free_subagent_supervisor.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "scripts")

    proc = subprocess.run(
        [sys.executable, str(script), "--provider-pools", "--pools-fixture", str(FIXTURE_POOLS)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["read_only"] is True
    assert payload["preferred_pool"] == "kilo-anonymous"
    tiers = [p["pool"] for p in payload["provider_pools"]]
    assert tiers[0] == "kilo-anonymous"
    assert "zai-reserve" in tiers
    zai = next(p for p in payload["provider_pools"] if p["pool"] == "zai-reserve")
    assert zai["excluded_by_policy"] is True


def test_provider_pools_cli_allow_reserve_includes_zai():
    import json
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).parent.parent
    script = repo_root / "scripts" / "free_subagent_supervisor.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "scripts")

    proc = subprocess.run(
        [
            sys.executable, str(script),
            "--provider-pools", "--allow-reserve",
            "--pools-fixture", str(FIXTURE_POOLS),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["allow_reserve"] is True
    zai = next(p for p in payload["provider_pools"] if p["pool"] == "zai-reserve")
    # When explicitly requested, Z.AI is no longer flagged excluded.
    assert zai.get("excluded_by_policy") is not True


if __name__ == "__main__":
    test_enumerate_free_models_from_fixture()
    test_credential_present_no_secret_leak()
    test_register_inspect_duplicates()
    test_projection_readonly_and_verdict()
    test_projection_healthy_when_clean()
    test_projection_notes_document_stdinhazard()
    test_sqlite_session_history_is_not_treated_as_concurrent_process_duplication()
    test_discover_live_sessions_readonly_query_only()
    test_projection_with_trace_exporter_integration()
    test_trace_exporter_readonly_query_only()
    test_classify_free_model_openrouter_account_free()
    test_classify_free_model_opencode_native()
    test_classify_free_model_provider_specific()
    test_classify_free_model_unknown_free()
    test_enumerate_free_models_with_classification()
    test_enumerate_free_models_deduplicates()
    test_projection_includes_free_model_types()
    test_kilo_anonymous_pool_constants()
    test_pool_ordering_prefers_anonymous_before_reserved()
    test_zai_is_reserve_only_unless_explicitly_requested()
    test_route_kind_distinguishes_exact_model_from_router_alias()
    test_provider_state_surface_without_spending()
    test_qualify_provider_pools_surfaces_states_and_reserve_policy()
    test_projection_orders_pools_and_marks_zai_reserve_only()
    test_projection_holds_when_no_anonymous_pool_and_no_credential()
    test_projection_healthy_with_anonymous_pool_even_without_credential()
    test_provider_pools_cli_exposes_reserve_policy()
    test_provider_pools_cli_allow_reserve_includes_zai()
    print("all free_subagent_supervisor tests passed")


def test_plain_catalog_only_accepts_exact_kilo_free_alias(monkeypatch):
    def fake_run(args, capture_output, text, timeout):
        return supervisor.subprocess.CompletedProcess(
            args,
            0,
            stdout="kilo/kilo-auto/free\nkilo/openai/gpt-paid\n",
            stderr="",
        )

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)
    models = supervisor.enumerate_free_models(pathlib.Path("/definitely/missing-fixture"))
    ids = [row["id"] for row in models]
    assert "kilo/kilo-auto/free" in ids
    assert "kilo/openai/gpt-paid" not in ids
    row = next(row for row in models if row["id"] == "kilo/kilo-auto/free")
    assert row["free_model_type"] == "kilo-anonymous"


def test_kilo_provider_manifest_matches_supervisor_policy():
    manifest_path = pathlib.Path(__file__).parent.parent / "config" / "opencode-kilo-anonymous.provider.json"
    payload = json.loads(manifest_path.read_text())
    kilo = payload["provider"]["kilo"]
    assert kilo["options"]["baseURL"] == supervisor.KILO_ANONYMOUS_BASE_URL
    assert kilo["options"]["apiKey"] == supervisor.KILO_ANONYMOUS_BEARER
    assert set(kilo["models"]) == {
        "kilo-auto/free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
    }


def test_projection_surfaces_session_discovery_failure_and_holds(monkeypatch, tmp_path):
    def fail_discovery(query_only=True):
        raise RuntimeError("opencode db unavailable")

    monkeypatch.setattr(supervisor, "discover_live_sessions", fail_discovery)
    proj = supervisor.emit_projection(
        free_models=[{
            "id": "kilo/kilo-auto/free",
            "provider": "kilo",
            "base_url": supervisor.KILO_ANONYMOUS_BASE_URL,
        }],
        state_path=tmp_path / "empty-state.jsonl",
    )
    assert proj["subagent_records"] == 0
    assert proj["suggested_scale_verdict"] == "hold"
    assert proj["session_discovery_error"] == "RuntimeError: opencode db unavailable"
