from datetime import UTC, datetime

from assistx.fleet_latency_collector import (
    build_latency_map,
    parse_ping_output,
    preferred_tailnet_targets,
)


NOW = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)


PING = """PING 100.64.0.2 (100.64.0.2) 56(84) bytes of data.
64 bytes from 100.64.0.2: icmp_seq=1 ttl=64 time=0.72 ms
64 bytes from 100.64.0.2: icmp_seq=2 ttl=64 time=0.83 ms
64 bytes from 100.64.0.2: icmp_seq=3 ttl=64 time=0.77 ms
64 bytes from 100.64.0.2: icmp_seq=4 ttl=64 time=1.01 ms

--- 100.64.0.2 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3003ms
"""


def _tailnet() -> dict:
    return {
        "authority": "candidate_reachability_only",
        "nodes": [
            {
                "node_id": "deathstar",
                "online": True,
                "admission_status": "candidate_only",
                "candidate_access_paths": [
                    {
                        "transport": "tailscale",
                        "base_url": "http://100.64.0.2:1234/v1",
                        "priority": 100,
                    },
                    {
                        "transport": "lan",
                        "base_url": "http://192.168.1.2:1234/v1",
                        "priority": 10,
                    },
                ],
            },
            {
                "node_id": "offline",
                "online": False,
                "candidate_access_paths": [
                    {
                        "transport": "tailscale",
                        "base_url": "http://100.64.0.3:1234/v1",
                        "priority": 100,
                    }
                ],
            },
        ],
    }


def test_parse_ping_uses_individual_samples() -> None:
    result = parse_ping_output(PING, transmitted=4)
    assert result["rtt_ms_p50"] == 0.8
    assert result["rtt_ms_p95"] == 1.01
    assert result["loss_rate"] == 0
    assert result["samples"] == 4


def test_preferred_target_preserves_existing_lan_priority() -> None:
    targets = preferred_tailnet_targets(_tailnet())
    assert targets == [
        {
            "node_id": "deathstar",
            "host": "192.168.1.2",
            "transport": "lan",
            "access_path": "http://192.168.1.2:1234/v1",
        }
    ]


def test_collector_does_not_admit_or_generate_endpoint_evidence() -> None:
    class Completed:
        stdout = PING

    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Completed()

    document, failures = build_latency_map(
        origin_node_id="x1-370",
        tailnet_candidates=_tailnet(),
        endpoint_evidence=[],
        pressures=[],
        run=fake_run,
        captured_at=NOW,
    )

    assert failures == []
    assert len(document.network_paths) == 1
    assert document.endpoints == []
    assert document.network_paths[0].target_node_id == "deathstar"
    assert document.network_paths[0].transport == "lan"
    assert set(document.authority.model_dump().values()) == {False}
    assert calls[0][0][:2] == ["ping", "-c"]


def test_probe_failure_is_retained_as_evidence_not_silently_promoted() -> None:
    class Completed:
        stdout = "4 packets transmitted, 0 received, 100% packet loss"

    def fake_run(argv, **kwargs):
        return Completed()

    document, failures = build_latency_map(
        origin_node_id="x1-370",
        tailnet_candidates=_tailnet(),
        run=fake_run,
        captured_at=NOW,
    )

    assert document.network_paths == []
    assert failures[0]["node_id"] == "deathstar"
    assert "no latency samples" in failures[0]["reason"]
