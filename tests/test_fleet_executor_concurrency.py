import json
import threading
import time

import assistx.fleet_executor as fleet_executor


class _DormantThread:
    started = []

    def __init__(self, *, target, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name

    def start(self):
        self.started.append(self)


def _llm_task(task_id: str) -> dict:
    return {
        "id": task_id,
        "required_capabilities": ["llm"],
        "payload": {"prompt": "test"},
    }


def test_process_tasks_acquires_one_llm_permit_per_worker(monkeypatch):
    executor = fleet_executor.FleetExecutor()
    executor._llm_sem = threading.Semaphore(2)
    executor._script_sem = threading.Semaphore(0)
    executor._refresh_nodes = lambda: None
    executor._pick_and_reserve_node = lambda *args, **kwargs: {"hostname": "worker", "ip": "127.0.0.1"}

    rows = [_llm_task(str(i)) for i in range(5)]
    monkeypatch.setattr(fleet_executor, "_http", lambda *args, **kwargs: (200, {"items": rows}))
    _DormantThread.started = []
    monkeypatch.setattr(fleet_executor.threading, "Thread", _DormantThread)

    executor._process_tasks()

    assert len(_DormantThread.started) == 2
    assert executor._llm_sem._value == 0


def test_script_lane_uses_strict_server_side_capability_filter(monkeypatch):
    executor = fleet_executor.FleetExecutor()
    executor._llm_sem = threading.Semaphore(0)
    executor._script_sem = threading.Semaphore(2)
    executor._refresh_nodes = lambda: None

    requested_urls = []

    def fake_http(method, url, **kwargs):
        requested_urls.append(url)
        if "capabilities=script" in url:
            return 200, {"items": []}
        return 200, {"items": [_llm_task("llm-through-script")]}

    monkeypatch.setattr(fleet_executor, "_http", fake_http)
    _DormantThread.started = []
    monkeypatch.setattr(fleet_executor.threading, "Thread", _DormantThread)

    executor._process_tasks()

    assert any("capabilities=script" in url for url in requested_urls)
    assert _DormantThread.started == []


def test_node_reservations_never_exceed_advertised_service_capacity():
    executor = fleet_executor.FleetExecutor()
    executor._nodes = [{
        "hostname": "llama-node",
        "ip": "100.64.0.9",
        "capabilities": ["llm"],
        "weight": 2,
        "last_seen": time.time(),
    }]

    assert executor._pick_and_reserve_node(["llm"]) is not None
    assert executor._pick_and_reserve_node(["llm"]) is not None
    assert executor._pick_and_reserve_node(["llm"]) is None
    executor._release_node("llama-node")
    assert executor._pick_and_reserve_node(["llm"]) is not None


def test_llamacpp_slot_count_is_discovered(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps([{"id": i} for i in range(4)]).encode()

    monkeypatch.setattr(
        fleet_executor.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    assert fleet_executor.FleetExecutor._probe_service_capacity("100.64.0.9") == 4
