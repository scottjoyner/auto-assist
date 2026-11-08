"""Socketless auth-denial backpressure without changing Task authority.

All identities, URLs and credentials are synthetic. No real network or graph.
"""

from __future__ import annotations

import pytest

from assistx import fleet_node_agent as agent


@pytest.fixture(autouse=True)
def _no_machine_identity(monkeypatch):
    for key in ("FLEET_NODE_TOKEN", "FLEET_NODE_IDENTITY_APPROVED_ORIGIN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(agent, "_detect_hostname", lambda: "synthetic-agent")
    monkeypatch.setattr(agent, "_auth_from_env", lambda: ("fake-basic", "fake-password"))
    monkeypatch.setattr(agent, "_detect_capabilities", lambda _: ([], []))


def _run(monkeypatch, statuses, *, poll_interval=2.0, cycles=5):
    """Return observed HTTP paths and sleep durations, then stop the loop."""
    attempted = []
    duration = []
    replies = iter(statuses)

    def fake_http(method, url, **_kwargs):
        attempted.append((method, url))
        if "/api/agent/tasks" in url:
            return next(replies, 401), {"tasks": []}
        assert method == "POST" and "/api/fleet/node-report" in url
        return 200, {}

    class EndSyntheticLoop(Exception):
        pass

    def sleep(seconds):
        duration.append(seconds)
        if len(duration) >= cycles:
            raise EndSyntheticLoop()

    monkeypatch.setattr(agent, "_http", fake_http)
    monkeypatch.setattr(agent.time, "sleep", sleep)
    with pytest.raises(EndSyntheticLoop):
        agent.run(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            poll_interval=poll_interval,
            concurrency=1,
            lmstudio_url=None,
        )
    return attempted, duration


def test_repeated_401_403_backoff_and_success_reset(monkeypatch):
    calls, sleeps = _run(monkeypatch, [401, 401, 403, 200, 401], cycles=5)
    assert sleeps == [2.0, 4.0, 8.0, 2.0, 2.0]
    assert len([c for c in calls if "/api/agent/tasks" in c[1]]) == 5
    assert all(c[0] in {"GET", "POST"} for c in calls)


def test_auth_backoff_saturates_without_big_int_growth(monkeypatch):
    _, sleeps = _run(monkeypatch, [401] * 17, poll_interval=5, cycles=17)
    assert sleeps[:5] == [5, 10, 20, 40, 80]
    assert sleeps[-1] == 300
    assert all(1 <= sleep <= 300 for sleep in sleeps)
    assert sleeps[7:] == [300] * 10


def test_403_alone_does_not_grant_task_authority(monkeypatch):
    _, sleeps = _run(monkeypatch, [403] * 4, poll_interval=0, cycles=4)
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_ordinary_server_failure_uses_normal_poll_interval(monkeypatch):
    _, sleeps = _run(monkeypatch, [401, 500, 401, 200], cycles=4)
    assert sleeps == [2.0, 2.0, 2.0, 2.0]


def test_invalid_auth_body_is_never_claimed(monkeypatch):
    seen = []
    monkeypatch.setattr(
        agent, "_http", lambda *_args, **_kwargs: (401, {"tasks": [{"id": "synthetic-should-never-run"}]})
    )
    monkeypatch.setattr(agent, "_claim_and_run", lambda **kwargs: seen.append(kwargs))
    denials = []
    assert (
        agent._poll_once(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            auth=("fake-basic", "fake-password"),
            node_id="synthetic-agent",
            caps=["script"],
            lmstudio_url=None,
            on_auth_denial=denials.append,
        )
        is False
    )
    assert denials == [401]
    assert seen == []


def test_machine_token_never_polls_legacy_tasks(monkeypatch):
    monkeypatch.setenv("FLEET_NODE_TOKEN", "synthetic-machine-token")
    monkeypatch.setenv("FLEET_NODE_ID", "synthetic-agent")
    monkeypatch.setattr(agent, "_http", lambda *_args, **_kwargs: pytest.fail("machine identity attempted legacy HTTP"))
    assert (
        agent._poll_once(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            auth=("fake-basic", "fake-password"),
            node_id="synthetic-agent",
            caps=["script"],
            lmstudio_url=None,
        )
        is False
    )
    with pytest.raises(RuntimeError, match="no legacy Task authority"):
        agent.run(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            poll_interval=2,
            concurrency=1,
            lmstudio_url=None,
        )


@pytest.mark.parametrize("setting", ["FLEET_NODE_TOKEN", "FLEET_NODE_IDENTITY_APPROVED_ORIGIN"])
def test_partial_or_empty_machine_identity_blocks_all_legacy_worker_side_effects(monkeypatch, setting):
    monkeypatch.setenv(setting, "")
    monkeypatch.setattr(
        agent, "_http", lambda *_a, **_k: pytest.fail("partial machine identity must not send Router or Task HTTP")
    )
    with pytest.raises(RuntimeError, match="no legacy Task authority"):
        agent.run(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            poll_interval=2,
            concurrency=1,
            lmstudio_url=None,
        )
    assert (
        agent._poll_once(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            auth=("valid-operator", "synthetic-not-real"),
            node_id="synthetic-agent",
            caps=["script"],
            lmstudio_url=None,
        )
        is False
    )


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), -1, -0.1, True, "2"])
def test_invalid_poll_interval_fails_before_any_network(monkeypatch, interval):
    monkeypatch.setattr(agent, "_http", lambda *_a, **_k: pytest.fail("must validate before any HTTP"))
    with pytest.raises(ValueError, match="invalid_poll_interval"):
        agent.run(
            assistx_url="http://127.0.0.1:7777",
            router_url="http://127.0.0.1:8888",
            poll_interval=interval,
            concurrency=1,
            lmstudio_url=None,
        )


def test_auth_denial_backoff_is_bounded_when_poll_interval_is_extremely_large(monkeypatch):
    _, sleeps = _run(monkeypatch, [401] * 12, poll_interval=600.0, cycles=12)
    assert sleeps == [300.0] * 12


def test_successful_poll_after_prior_denial_resets_retry_counter(monkeypatch):
    _, sleeps = _run(monkeypatch, [401, 403, 200, 401, 403], poll_interval=1.0, cycles=5)
    assert sleeps == [1.0, 2.0, 1.0, 1.0, 2.0]
