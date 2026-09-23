from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-kipnerter-agent-auto-live.sh"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_live_verifier_is_exact_sha_and_clean_worktree_gated() -> None:
    text = _text()
    assert "EXPECTED_SHA is required" in text
    assert "git rev-parse HEAD" in text
    assert "git status --porcelain" in text
    assert "worktree is dirty; refusing live recreate" in text


def test_live_verifier_recreates_only_api_and_never_mutates_serve() -> None:
    text = _text()
    assert '"${COMPOSE[@]}" build api' in text
    assert '"${COMPOSE[@]}" up -d --no-deps --force-recreate api' in text
    assert "tailscale serve status --json" in text
    assert "tailscale serve set" not in text
    assert "tailscale serve reset" not in text
    assert "tailscale funnel" not in text


def test_live_verifier_preserves_credential_and_authority_boundaries() -> None:
    text = _text()
    assert "FLEET_ROUTER_BEARER_TOKEN" in text
    assert '"router_token_present": bool(' in text
    assert "AUTO_ROUTER_ADMIN_TOKEN" not in text
    assert "x-assistx-executor-identity: spoofed-executor" in text
    assert "expected 401" in text
    assert "/api/v1/agent/chat/completions" in text
    assert "x-kipnerter-agent-executor" in text.lower()
    assert "/api/v1/runtime/catalog" not in text


def test_live_verifier_has_exactly_one_chat_request_site() -> None:
    text = _text()
    assert text.count("$KIPNERTER_GATEWAY_URL/api/v1/agent/chat/completions") == 1
