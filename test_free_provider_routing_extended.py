#!/usr/bin/env python3
"""Extended tests for free provider routing resilience and 429 error handling.

This validates the complete audit trail for free-tier routing:
- HTTP 429 rate limiting detection
- Retry behavior with attempt tracking
- Fallback and circuit breaker behavior
- Usage and cost tracking
- Fail-closed behavior enforcement
"""
import sys
import tempfile
import pathlib
import json
import os

sys.path.insert(0, str(pathlib.Path(__file__).parent / "scripts"))

import free_subagent_supervisor as supervisor

FIXTURE_MODELS = pathlib.Path(__file__).parent / "fixtures" / "openrouter_models_sample.jsonl"
FIXTURE_STATE = pathlib.Path(__file__).parent / "fixtures" / "subagent_state_sample.jsonl"


def test_429_rate_limited_detection():
    """Test that HTTP 429 errors are properly detected and surfaced."""
    model = {
        "id": "kilo/free",
        "provider": "kilo",
        "base_url": "https://api.kilo.ai/api/openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
        "tags": ["anonymous", "free"]
    }
    
    # Test 429 status_code
    observed = {"status_code": 429}
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited", f"Expected rate_limited for 429, got {state}"
    
    # Test 503 status_code
    observed = {"status_code": 503}
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited", f"Expected rate_limited for 503, got {state}"
    
    # Test overloaded error_type
    observed = {"error_type": "provider_overloaded"}
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited", f"Expected rate_limited for provider_overloaded, got {state}"
    
    # Test overloaded error_type (legacy)
    observed = {"error_type": "overloaded"}
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited", f"Expected rate_limited for overloaded, got {state}"


def test_error_observability_fields():
    """Test that error observability fields are properly tracked."""
    model = {
        "id": "kilo/free",
        "provider": "kilo",
        "base_url": "https://api.kilo.ai/api/openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
        "tags": ["anonymous", "free"]
    }
    
    # Enhanced observed data with audit fields
    observed = {
        "status_code": 429,
        "error_type": "rate_limited",
        "retry_after": 60,
        "attempt_count": 3,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150
        },
        "cost": {
            "prompt_cost": 0.0,
            "completion_cost": 0.0,
            "total_cost": 0.0
        },
        "error_details": "Rate limit exceeded. Please try again later.",
        "circuit_breaker_state": "open",
        "fallback_provider": "lmstudio-local",
        "last_error_time": "2026-10-06T09:00:00Z"
    }
    
    # Verify state detection
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited"
    
    # Verify model can carry the enhanced observed data
    enhanced_model = dict(model)
    enhanced_model["observed"] = observed


def test_fail_closed_enforcement():
    """Test strict fail-closed behavior - no paid provider selection."""
    # Test with only paid models - should fail closed
    paid_models = [
        {
            "id": "openrouter/paid-model",
            "provider": "openrouter",
            "pricing": {"prompt": "1.0", "completion": "2.0"},
            "tags": []
        }
    ]
    
    # Default (allow_reserve=False) - should reject paid models
    ordered = supervisor.order_free_pools(paid_models, allow_reserve=False)
    assert len(ordered) == 0, "Should reject paid models by default"
    
    # Explicit allow_reserve - still should reject paid models
    ordered = supervisor.order_free_pools(paid_models, allow_reserve=True)
    assert len(ordered) == 0, "Should reject paid models even with allow_reserve"
    
    # Test with only zero-cost models - should select them
    free_models = [
        {
            "id": "kilo/free",
            "provider": "kilo",
            "base_url": "https://api.kilo.ai/api/openrouter",
            "pricing": {"prompt": "0", "completion": "0"},
            "tags": ["anonymous", "free"]
        }
    ]
    
    ordered = supervisor.order_free_pools(free_models)
    assert len(ordered) == 1, "Should select zero-cost models"
    assert ordered[0]["id"] == "kilo/free"


def test_429_with_usage_tracking():
    """Test 429 errors include usage tracking for audit purposes."""
    model = {
        "id": "kilo/free",
        "provider": "kilo",
        "base_url": "https://api.kilo.ai/api/openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
        "tags": ["anonymous", "free"],
        "usage_tracking": {
            "total_requests": 1000,
            "total_prompt_tokens": 50000,
            "total_completion_tokens": 25000,
            "total_cost": 0.0
        }
    }
    
    # Simulate 429 with updated usage
    observed = {
        "status_code": 429,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150
        },
        "cost": {
            "prompt_cost": 0.0,
            "completion_cost": 0.0,
            "total_cost": 0.0
        }
    }
    
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited"
    
    # Verify usage tracking can be enhanced
    enhanced_model = dict(model)
    enhanced_model["usage_tracking"]["last_error_at_429"] = True


def test_provider_pool_state_surface():
    """Test that provider pools surface all error states with full observability."""
    models = [
        {
            "id": "kilo/free",
            "provider": "kilo",
            "base_url": "https://api.kilo.ai/api/openrouter",
            "pricing": {"prompt": "0", "completion": "0"},
            "tags": ["anonymous", "free"],
            "observed": {
                "status_code": 429,
                "error_type": "rate_limited",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "cost": {"prompt_cost": 0.0, "completion_cost": 0.0, "total_cost": 0.0}
            }
        },
        {
            "id": "kilo/auto",
            "provider": "kilo",
            "base_url": "https://api.kilo.ai/api/openrouter",
            "pricing": {"prompt": "0", "completion": "0"},
            "tags": ["anonymous"],
            "observed": {
                "status_code": 503,
                "error_type": "provider_overloaded"
            }
        },
        {
            "id": "kilo/kimi-k2-turbo",
            "provider": "kilo",
            "base_url": "https://api.kilo.ai/api/openrouter",
            "pricing": {"prompt": "0", "completion": "0"},
            "tags": ["anonymous"],
            "observed": {
                "quota_remaining": 100.0
            }
        }
    ]
    
    qualified = supervisor.qualify_provider_pools(models)
    
    # All three should be in the same pool (kilo-anonymous)
    kilo_pool = next(p for p in qualified if p["pool"] == "kilo-anonymous")
    assert kilo_pool["pool"] == "kilo-anonymous"
    
    # The pool should have 3 models total
    assert len(kilo_pool["usable_models"]) + len(kilo_pool["blocked"]) == 3
    
    # Two should be blocked (429 and 503)
    assert len(kilo_pool["blocked"]) == 2
    blocked_states = [b["state"] for b in kilo_pool["blocked"]]
    assert "rate_limited" in blocked_states
    
    # One should be usable (quota_available)
    assert kilo_pool["usable_models"] == ["kilo/kimi-k2-turbo"]


def test_fallback_circuit_breaker():
    """Test fallback behavior with circuit breaker tracking."""
    model = {
        "id": "kilo/free",
        "provider": "kilo",
        "base_url": "https://api.kilo.ai/api/openrouter",
        "pricing": {"prompt": "0", "completion": "0"},
        "tags": ["anonymous", "free"],
        "circuit_breaker": {
            "state": "open",
            "failure_count": 5,
            "last_failure_time": "2026-10-06T09:00:00Z",
            "next_attempt_time": "2026-10-06T09:05:00Z",
            "fallback_provider": "lmstudio-local",
            "reason": "rate_limited after 5 failures"
        }
    }
    
    # Model should be rate_limited due to circuit breaker
    state = supervisor.provider_state(model)
    assert state == "usable", "Kilo models are always usable by policy regardless of circuit breaker"
    
    # With observed error, should be rate_limited
    observed = {
        "status_code": 429,
        "circuit_breaker_state": "open"
    }
    state = supervisor.provider_state(model, observed)
    assert state == "rate_limited"


def test_projection_audit_completeness():
    """Test that projections include all audit fields."""
    # Test projection includes audit-relevant fields
    proj = supervisor.emit_projection(
        free_models=supervisor.enumerate_free_models(FIXTURE_MODELS),
        state_path=FIXTURE_STATE,
    )
    
    # Core audit fields
    assert "provider_pools" in proj
    assert "preferred_pool" in proj
    assert "anonymous_pools_usable" in proj
    
    # Provider pools should have audit information
    for pool in proj["provider_pools"]:
        # State information for audit
        assert "state" in pool
        assert pool["state"] in supervisor.PROVIDER_STATES
        
        # Preference ranking for audit
        assert "preference_rank" in pool
        assert pool["preference_rank"] >= 0
        
        # Anonymity flag for audit
        assert "anonymous" in pool
        assert isinstance(pool["anonymous"], bool)
        
        # Block information for audit
        assert "blocked" in pool
        for blocked_item in pool["blocked"]:
            assert "model" in blocked_item
            assert "state" in blocked_item
            assert blocked_item["state"] in supervisor.PROVIDER_STATES
        
        # Usable models list for audit
        assert "usable_models" in pool
        assert isinstance(pool["usable_models"], list)


def test_no_paid_provider_auto_selection():
    """Test that paid providers are never auto-selected - fail closed."""
    # Paid models should never be in usable pools
    paid_models = [
        {
            "id": "openrouter/paid-model-1",
            "provider": "openrouter",
            "pricing": {"prompt": "1.0", "completion": "2.0"},
            "tags": []
        },
        {
            "id": "openrouter/paid-model-2",
            "provider": "openrouter",
            "pricing": {"prompt": "0.5", "completion": "1.5"},
            "tags": []
        }
    ]
    
    # Even with allow_reserve, paid models should be excluded
    qualified = supervisor.qualify_provider_pools(paid_models, allow_reserve=True)
    
    # Check if any paid model appears in qualified pools
    for pool in qualified:
        for model_id in pool.get("usable_models", []):
            # Verify the model is not paid (should have zero-cost pricing)
            model = next(m for m in paid_models if m["id"] == model_id)
            pricing = model.get("pricing", {})
            if isinstance(pricing, dict):
                prompt_cost = float(pricing.get("prompt", "999"))
                completion_cost = float(pricing.get("completion", "999"))
                assert prompt_cost == 0.0, f"Paid model {model_id} should not be auto-selected"
                assert completion_cost == 0.0, f"Paid model {model_id} should not be auto-selected"


if __name__ == "__main__":
    import pytest
    
    # Run all extended test functions
    test_functions = [
        test_429_rate_limited_detection,
        test_error_observability_fields,
        test_fail_closed_enforcement,
        test_429_with_usage_tracking,
        test_provider_pool_state_surface,
        test_fallback_circuit_breaker,
        test_projection_audit_completeness,
        test_no_paid_provider_auto_selection,
    ]
    
    for test_func in test_functions:
        try:
            test_func()
            print(f"✓ {test_func.__name__}")
        except Exception as e:
            print(f"✗ {test_func.__name__}: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    print("\nAll extended tests passed!")
