"""Synthetic /proc fixtures: never load models, open real network, or sign approval."""
import importlib.util
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

SRC = Path(__file__).resolve().parents[1] / "scripts/inspect-x1-k2-physical-witness-readonly.py"
SPEC = importlib.util.spec_from_file_location("physical_witness", SRC)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _stat(*, ppid: int, ticks: int, comm: str = "comm ) with spaces") -> str:
    fields = ["0"] * 50
    fields[0] = "S"
    fields[1] = str(ppid)
    fields[19] = str(ticks)
    return "123 (" + comm + ") " + " ".join(fields)


def _tcp_line(ip: str, port: int, inode: int) -> str:
    addr = socket.inet_aton(ip)[::-1].hex().upper()
    return (
        f"0: {addr}:{port:04X} 00000000:0000 0A "
        f"0:0 0:0 0 0 0 {inode} 0 0"
    )


def _create_fixture(tmp_path, *, socket_tailscale=True, model_mapped=True,
                    multiple_root=False):
    root = tmp_path / "proc"
    exe = tmp_path / "lm-studio"
    exe.write_bytes(b"the exact synthetic executable bytes")
    socat = tmp_path / "socat"
    socat.write_bytes(b"synthetic socat executable")
    model = tmp_path / "k2.gguf"
    model.write_bytes(b"synthetic GGUF, never admitted")
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"version": module.REVIEWED_VERSION}))
    for pid, parent in [(123, 1), (124, 123)]:
        target = root / str(pid)
        (target / "fd").mkdir(parents=True)
        (target / "stat").write_text(_stat(ppid=parent, ticks=555))
        cmd = ([str(exe), "--run-as-service"] if pid == 123
               else [str(exe), "--type=zygote"])
        (target / "cmdline").write_bytes(b"\x00".join(x.encode() for x in cmd) + b"\x00")
        (target / "maps").write_text(
            ("0000-2000 r--p 00000000 00:00 1 " + str(model) + "\n")
            if pid == 124 and model_mapped else "0000-2000 r--p 00:00 1 irrelevant\n"
        )
        if pid == 123:
            (target / "exe").symlink_to(exe)
    (root / "124" / "fd" / "0").symlink_to("socket:[111]")
    for proxy_pid, ip, inode in (
        (125, "192.168.1.237", 112),
        (126, "100.64.43.123", 113),
    ):
        target = root / str(proxy_pid)
        (target / "fd").mkdir(parents=True)
        (target / "stat").write_text(_stat(ppid=1, ticks=proxy_pid * 10))
        cmd = [
            str(socat),
            "TCP-LISTEN:1234,bind=" + ip + ",fork,reuseaddr",
            "TCP:127.0.0.1:1234",
        ]
        (target / "cmdline").write_bytes(
            b"\x00".join(x.encode() for x in cmd) + b"\x00"
        )
        (target / "maps").write_text("0000-2000 r--p 00:00 1 irrelevant\n")
        (target / "exe").symlink_to(socat)
        (target / "fd" / "0").symlink_to("socket:[" + str(inode) + "]")
    tcp = [
        _tcp_line("127.0.0.1", 1234, 111),
        _tcp_line("192.168.1.237", 1234, 112),
        _tcp_line("100.64.43.123", 1234, 113 if socket_tailscale else 999),
        _tcp_line("172.17.0.1", 1234, 987),
        _tcp_line("127.0.0.1", 8123, 555),
    ]
    (root / "net").mkdir()
    (root / "net" / "tcp").write_text("sl local rem st tx rx tr tm retr uid timeout inode\n" + "\n".join(tcp))
    (root / "net" / "tcp6").write_text("sl local rem st tx rx tr tm retr uid timeout inode\n")
    boot = root / "sys/kernel/random"
    boot.mkdir(parents=True)
    (boot / "boot_id").write_text("synthetic-test-boot-uuid\n")
    if multiple_root:
        root2 = root / "888"
        root2.mkdir()
        (root2 / "stat").write_text(_stat(ppid=1, ticks=556))
        (root2 / "cmdline").write_bytes(
            str(exe).encode() + b"\x00--run-as-service\x00"
        )
    return root, exe, model, pkg


def _observe_fixture(tmp_path, *, loaded=True, model_mapped=True,
                     socket_tailscale=True, duplicate=False):
    root, exe, model, pkg = _create_fixture(
        tmp_path, model_mapped=model_mapped,
        socket_tailscale=socket_tailscale, multiple_root=duplicate
    )
    original_digest = module.digest_file

    def digest(path):
        return module.EXPECTED_MODEL_SHA if path == model else original_digest(path)

    catalog_data = {"reachable": True, "loaded_count": int(loaded),
                    "expected_model_present": loaded}
    with patch.object(module, "EXECUTABLE", exe), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"), \
         patch.object(module, "MODEL", model), \
         patch.object(module, "PACKAGE", pkg), \
         patch.object(module, "EXPECTED_MODEL_BYTES", model.stat().st_size), \
         patch.object(module, "digest_file", side_effect=digest), \
         patch.object(module, "catalog", return_value=catalog_data):
        return module.observe(proc_root=root, read_model_sha=True)


def test_parses_proc_stat_with_embedded_parens(tmp_path):
    p = tmp_path / "stat"
    p.write_text(_stat(ppid=99, ticks=123456))
    assert module.proc_stat(p) == {"ppid": 99, "start_ticks": 123456}


def test_exact_listener_inodes_and_process_owner(tmp_path):
    root, _, _, _ = _create_fixture(tmp_path)
    sockets = module.listener_inodes(root)
    assert sockets["127.0.0.1"] == {111}
    assert sockets["192.168.1.237"] == {112}
    assert sockets["100.64.43.123"] == {113}
    server_owned = module.process_socket_inodes(root, [123, 124])
    assert server_owned & sockets["127.0.0.1"]
    assert not (server_owned & sockets["192.168.1.237"])
    assert not (server_owned & sockets["100.64.43.123"])
    assert module.process_socket_inodes(root, [125]) & sockets["192.168.1.237"]
    assert module.process_socket_inodes(root, [126]) & sockets["100.64.43.123"]
    with patch.object(module, "EXECUTABLE", tmp_path / "lm-studio"), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"):
        tree = module.process_tree(root)
    assert tree["descendants"] == [123, 124]
    assert tree["proxies"]["lan"]["pid"] == 125
    assert tree["proxies"]["tailscale"]["pid"] == 126


def test_full_synthetic_local_chain_still_never_admits(tmp_path):
    report = _observe_fixture(tmp_path)
    assert report["physical_process_socket_chain_verified"]
    assert report["model_loaded_physically_verified"]
    assert report["process"]["model_file_mapped_in_process_tree"] is True
    assert report["process"]["installed_executable_sha256_match"] is True
    assert report["gate"] == "HOLD"
    assert report["can_dispatch"] is False
    assert report["operator_approval_verified"] is False
    assert report["cryptographic_witness_signature_present"] is False
    assert "independent_signed_witness_and_approval_missing" in report["reasons"]


def test_tailscale_socket_not_owned_is_hold(tmp_path):
    report = _observe_fixture(tmp_path, socket_tailscale=False)
    assert not report["physical_process_socket_chain_verified"]
    assert not report["model_loaded_physically_verified"]
    assert not report["process"]["per_transport_socket_owner_verified"]["tailscale"]


def test_model_visible_but_not_mapped_cannot_pass_physical(tmp_path):
    report = _observe_fixture(tmp_path, model_mapped=False)
    assert report["physical_process_socket_chain_verified"]
    assert not report["model_loaded_physically_verified"]
    assert "model_file_not_mapped_by_runtime" in report["reasons"]


def test_api_model_absent_cannot_prove_model_loaded(tmp_path):
    report = _observe_fixture(tmp_path, loaded=False)
    assert report["physical_process_socket_chain_verified"]
    assert not report["model_loaded_physically_verified"]
    assert "expected_model_missing_on_one_or_more_paths" in report["reasons"]


def test_two_candidate_runtime_processes_are_ambiguous(tmp_path):
    report = _observe_fixture(tmp_path, duplicate=True)
    assert not report["physical_process_socket_chain_verified"]
    assert not report["model_loaded_physically_verified"]
    assert "process_identity_or_socket_unverified" in report["reasons"]


def test_process_executable_unreadable_is_hold(tmp_path):
    root, exe, model, pkg = _create_fixture(tmp_path)
    real = module.os.readlink
    def deny(path):
        if str(path).endswith("/123/exe"):
            raise PermissionError("synthetic proc restriction")
        return real(path)
    with patch.object(module, "EXECUTABLE", exe), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"), \
         patch.object(module, "MODEL", model), \
         patch.object(module, "PACKAGE", pkg), \
         patch.object(module.os, "readlink", side_effect=deny), \
         patch.object(module, "catalog", return_value={"reachable": True,
                         "expected_model_present": True, "loaded_count": 1}):
        report = module.observe(proc_root=root, read_model_sha=False)
    assert report["gate"] == "HOLD"
    assert report["can_dispatch"] is False
    assert report["process"]["collection_error_kind"] == "PermissionError"


def test_redirects_never_follow_foreign_hosts():
    with pytest.raises(ValueError, match="redirect"):
        module.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "http://8.8.8.8/models"
        )


def test_socket_symlink_inventory_failure_is_error(tmp_path):
    root, _, _, _ = _create_fixture(tmp_path)
    with pytest.raises(ValueError, match="proc_fd_readlink_unavailable"):
        with patch.object(module.os, "readlink", side_effect=PermissionError):
            module.process_socket_inodes(root, [123, 124])


def test_model_symlink_rejected_even_if_digest_matches(tmp_path):
    root, exe, model, pkg = _create_fixture(tmp_path)
    model2 = tmp_path / "model-symlink.gguf"
    model2.symlink_to(model)
    with patch.object(module, "EXECUTABLE", exe), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"), \
         patch.object(module, "MODEL", model2), \
         patch.object(module, "PACKAGE", pkg), \
         patch.object(module, "EXPECTED_MODEL_BYTES", model.stat().st_size), \
         patch.object(module, "catalog", return_value={"reachable": True,
             "expected_model_present": True, "loaded_count": 1}):
        report = module.observe(proc_root=root, read_model_sha=True)
    assert report["model_artifact_sha256_match"] is False
    assert report["gate"] == "HOLD"
    assert not report["model_loaded_physically_verified"]


def test_reused_runtime_pid_revokes_local_identity(tmp_path):
    root, exe, model, pkg = _create_fixture(tmp_path)
    original_stat = module.proc_stat
    calls = {"root_stat": 0}

    def changed_on_recheck(path):
        result = original_stat(path)
        if str(path) == str(root / "123" / "stat"):
            calls["root_stat"] += 1
            if calls["root_stat"] >= 2:
                result["start_ticks"] += 1
        return result

    with patch.object(module, "EXECUTABLE", exe), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"), \
         patch.object(module, "MODEL", model), \
         patch.object(module, "PACKAGE", pkg), \
         patch.object(module, "EXPECTED_MODEL_BYTES", model.stat().st_size), \
         patch.object(module, "digest_file", side_effect=(
             lambda path: module.EXPECTED_MODEL_SHA if path == model
             else __import__("hashlib").sha256(path.read_bytes()).hexdigest()
         )), \
         patch.object(module, "catalog", return_value={"reachable": True,
             "expected_model_present": True, "loaded_count": 1}), \
         patch.object(module, "proc_stat", side_effect=changed_on_recheck):
        report = module.observe(proc_root=root)
    assert calls["root_stat"] >= 2
    assert "runtime_pid_reused_or_disappeared" in report["reasons"]
    assert not report["physical_process_socket_chain_verified"]
    assert not report["model_loaded_physically_verified"]
    assert not report["can_dispatch"]



def test_missing_expected_transport_forwarder_is_hold(tmp_path):
    root, exe, model, pkg = _create_fixture(tmp_path)
    (root / "125" / "cmdline").write_bytes(
        b"/usr/bin/other\x00TCP-LISTEN:1234,bind=192.168.1.237,fork,reuseaddr\x00"
    )
    with patch.object(module, "EXECUTABLE", exe), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"), \
         patch.object(module, "MODEL", model), \
         patch.object(module, "PACKAGE", pkg), \
         patch.object(module, "catalog", return_value={"reachable": True,
             "expected_model_present": True, "loaded_count": 1}):
        report = module.observe(proc_root=root, read_model_sha=False)
    assert "process_identity_or_socket_unverified" in report["reasons"]
    assert report["physical_process_socket_chain_verified"] is False
    assert report["can_dispatch"] is False


def test_observer_emits_exact_measured_artifact_bytes_and_digest(tmp_path):
    report = _observe_fixture(tmp_path)
    assert report["model_id_expected"] == module.MODEL_ID
    assert report["model_artifact_sha256_observed"] == module.EXPECTED_MODEL_SHA
    assert report["model_artifact_bytes_observed"] == (
        tmp_path / "k2.gguf"
    ).stat().st_size
    assert report["model_artifact_sha256_match"] is True
    assert report["process"]["running_executable_sha256"] == module.digest_file(
        tmp_path / "lm-studio"
    )
    for proxy in report["process"]["proxies"].values():
        assert proxy["running_executable_sha256"] == module.digest_file(
            tmp_path / "socat"
        )


def test_observer_size_mismatch_rejects_without_hashing_wrong_file(tmp_path):
    root, exe, model, pkg = _create_fixture(tmp_path)
    with patch.object(module, "EXECUTABLE", exe), \
         patch.object(module, "PROXY_EXECUTABLE", tmp_path / "socat"), \
         patch.object(module, "MODEL", model), \
         patch.object(module, "PACKAGE", pkg), \
         patch.object(module, "EXPECTED_MODEL_BYTES", model.stat().st_size + 1), \
         patch.object(module, "digest_file", wraps=module.digest_file) as hashed, \
         patch.object(module, "catalog", return_value={
             "reachable": True, "loaded_count": 1,
             "expected_model_present": True,
         }):
        report = module.observe(proc_root=root)
    assert report["model_artifact_bytes_observed"] == model.stat().st_size
    assert report["model_artifact_sha256_observed"] is None
    assert report["model_artifact_sha256_match"] is False
    assert not any(call.args[0] == model for call in hashed.call_args_list)
    assert report["can_dispatch"] is False
