import assistx.worker as worker_mod


def test_worker_name_includes_hostname_and_pid(monkeypatch):
    monkeypatch.setattr(worker_mod.socket, "gethostname", lambda: "test-host.example.local")
    monkeypatch.setattr(worker_mod.os, "getpid", lambda: 4321)

    assert worker_mod._worker_name(2) == "assistx-worker-2-test-host-4321"
