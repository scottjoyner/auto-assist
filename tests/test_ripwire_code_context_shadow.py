"""Synthetic offline Ripwire navigation safety tests; no Docker/model execution."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "research" / "ripwire_code_context_shadow.py"
SPEC = importlib.util.spec_from_file_location("ripwire_context_shadow_test", SOURCE)
shadow = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = shadow
SPEC.loader.exec_module(shadow)
TASK = "Locate signed witness policy epoch validation logic"


def payload(task: str = TASK, **overrides) -> bytes:
    data = {
        "task": task,
        "route": "subtoken+body",
        "confidence": "low",
        "est_tokens": 2698,
        "budget_tokens": 2400,
        "capped": True,
        "sigs": [
            {"p": "research/gate.py", "n": "validate", "r": 1, "l": 10},
            {"p": "src/journal.rs", "n": "append", "r": 2, "l": 20},
            {"p": "research/gate.py", "n": "check", "r": 3, "l": 33},
        ],
    }
    data.update(overrides)
    return json.dumps(data, separators=(",", ":")).encode()


def fixture_dirs(base: Path) -> tuple[Path, Path]:
    repo = base / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "research").mkdir()
    (repo / "src" / "journal.rs").write_text("fn append() {}\n")
    (repo / "research" / "gate.py").write_text("def validate(): pass\n")
    snap = base / "snapshot"
    snap.mkdir()
    shadow.stage_source_only(repo, snap)
    return repo, snap


def audit(raw: bytes, snapshot: Path, task: str = TASK) -> dict:
    return shadow.audit_ripwire_json(raw, task=task, snapshot=snapshot)


class RipwireContextShadowTests(unittest.TestCase):
    def test_pins_exact_public_release(self):
        self.assertEqual(shadow.VERSION, "v0.6.9")
        self.assertEqual(len(shadow.RELEASE_SHA256), 64)
        self.assertEqual(len(shadow.BINARY_SHA256), 64)

    def test_small_binary_with_explicit_known_digest_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "ripwire"
            target.write_bytes(b"synthetic-no-execution")
            sha = hashlib.sha256(target.read_bytes()).hexdigest()
            shadow.verify_binary(target, expected_sha256=sha)

    def test_wrong_binary_hash_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "ripwire"
            target.write_bytes(b"not-the-pinned-release")
            with self.assertRaises(shadow.ContextShadowError):
                shadow.verify_binary(target)

    def test_binary_symlink_denied(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "real"
            target.write_bytes(b"test")
            link = Path(d) / "ripwire"
            link.symlink_to(target)
            with self.assertRaises(shadow.ContextShadowError):
                shadow.verify_binary(link)

    def test_binary_size_cap_denies_sparse_oversize(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "ripwire"
            with target.open("wb") as f:
                f.truncate(75_000_001)
            with self.assertRaises(shadow.ContextShadowError):
                shadow.verify_binary(target)

    def test_directory_disguised_as_binary_denied(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(shadow.ContextShadowError):
                shadow.verify_binary(Path(d))

    def test_missing_binary_denied_without_path_leak(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(shadow.ContextShadowError) as exc:
                shadow.verify_binary(Path(d) / "private-name")
            self.assertNotIn("private-name", str(exc.exception))

    def test_source_copy_only_code_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo, snap = fixture_dirs(root)
            (repo / "src" / "secret.env").write_text("TOKEN=secret")
            (repo / "research" / "private.jsonl").write_text('{"payload":1}')
            (repo / "research" / "private.wav").write_bytes(b"audio")
            (repo / "src" / ".hidden.py").write_text("hidden")
            (repo / "research" / "symlink.py").symlink_to(
                repo / "research" / "gate.py"
            )
            second = root / "new_snapshot"
            second.mkdir()
            report = shadow.stage_source_only(repo, second)
            self.assertEqual(report["files"], 2)
            self.assertGreaterEqual(report["excluded"], 4)
            self.assertFalse((second / "src" / "secret.env").exists())
            self.assertFalse((second / "research" / "private.jsonl").exists())
            self.assertFalse((second / "research" / "private.wav").exists())
            self.assertFalse((second / "src" / ".hidden.py").exists())
            self.assertFalse((second / "research" / "symlink.py").exists())
            self.assertTrue((second / "src" / "journal.rs").is_file())

    def test_untrusted_oversized_source_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            repo, _ = fixture_dirs(Path(d))
            (repo / "src" / "huge.rs").write_bytes(b"x" * 200_001)
            snapshot = Path(d) / "third"
            snapshot.mkdir()
            result = shadow.stage_source_only(repo, snapshot)
            self.assertEqual(result["files"], 2)
            self.assertGreaterEqual(result["excluded"], 1)

    def test_root_symlink_denied(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            repo, _ = fixture_dirs(base)
            link = base / "linked_repo"
            link.symlink_to(repo)
            target = base / "new"
            target.mkdir()
            with self.assertRaises(shadow.ContextShadowError):
                shadow.stage_source_only(link, target)

    def test_empty_source_root_denied(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "repo" / "src").mkdir(parents=True)
            snapshot = base / "snap"
            snapshot.mkdir()
            with self.assertRaises(shadow.ContextShadowError):
                shadow.stage_source_only(base / "repo", snapshot)

    def test_non_absolute_root_denied(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(shadow.ContextShadowError):
                shadow.stage_source_only(Path("relative"), Path(d))

    def test_valid_ranking_is_bounded_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            result = audit(payload(), snap)
            self.assertEqual(result["confidence_reported_by_tool"], "low")
            self.assertEqual(result["production_gate"], "HOLD")
            self.assertEqual(len(result["candidate_files"]), 2)
            self.assertEqual(result["candidate_files"][0], {
                "relative_file": "research/gate.py", "first_symbol_rank": 1
            })
            self.assertEqual(result["candidate_files"][1], {
                "relative_file": "src/journal.rs", "first_symbol_rank": 2
            })
            self.assertTrue(result["reported_budget_exceeded"])
            self.assertFalse(result["model_quality_verified"])
            self.assertFalse(result["network_egress_allowed"])
            self.assertFalse(result["source_custody_verified"])
            self.assertEqual(result["model_tokens_used"], 0)

    def test_task_does_not_leave_plaintext_in_report(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            text = json.dumps(audit(payload(), snap))
            self.assertNotIn(TASK, text)
            self.assertNotIn("def validate", text)
            self.assertNotIn("fn append", text)

    def test_reported_under_budget_is_not_policy_authority(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            r = audit(payload(est_tokens=1200), snap)
            self.assertFalse(r["reported_budget_exceeded"])
            self.assertEqual(r["production_gate"], "HOLD")
            self.assertFalse(r["independent_relevance_verified"])

    def test_different_task_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            with self.assertRaises(shadow.ContextShadowError):
                audit(payload(), snap, task="another task")

    def test_non_utf8_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            with self.assertRaises(shadow.ContextShadowError):
                audit(b"\xff\xfe", snap)

    def test_malformed_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            with self.assertRaises(shadow.ContextShadowError):
                audit(b"{bad", snap)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            raw = payload().replace(b'"capped":true', b'"capped":true,"capped":true')
            with self.assertRaises(shadow.ContextShadowError):
                audit(raw, snap)

    def test_nan_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            with self.assertRaises(shadow.ContextShadowError):
                audit(payload().replace(b"2698", b"NaN"), snap)

    def test_output_max_bytes_denied(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            with self.assertRaises(shadow.ContextShadowError):
                audit(b"x" * (shadow.OUTPUT_MAX_BYTES + 1), snap)

    def test_nonbytes_input_denied(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for item in (None, [], "{}", True):
                with self.subTest(v=str(item)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(item, snap)

    def test_invalid_confidence_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for item in (None, True, 5, "excellent", ""):
                with self.subTest(v=str(item)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(confidence=item), snap)

    def test_invalid_estimate_and_budget(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for item in (None, -1, 500_001, True, "2000"):
                with self.subTest(v=str(item)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(est_tokens=item), snap)
            for budget in (None, True, 0, 1200, "2400"):
                with self.subTest(budget=str(budget)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(budget_tokens=budget), snap)

    def test_invalid_capped_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for value in (None, 1, "true"):
                with self.subTest(value=str(value)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(capped=value), snap)

    def test_invalid_signature_list_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for value in (None, {}, True, [{}] * 129):
                with self.subTest(v=str(value)[:35]), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(sigs=value), snap)

    def test_duplicate_rank_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            entries = [
                {"p": "src/journal.rs", "r": 1},
                {"p": "research/gate.py", "r": 1},
            ]
            with self.assertRaises(shadow.ContextShadowError):
                audit(payload(sigs=entries), snap)

    def test_rank_bool_or_negative_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for rank in (True, -1, 0, None, "1", 100_001):
                with self.subTest(rank=str(rank)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(sigs=[{"p": "src/journal.rs", "r": rank}]), snap)

    def test_path_traversal_and_absolute_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            bad = (
                "/etc/passwd", "../secrets.txt", "src/../../secrets.py",
                "src/.env.py", "src\\journal.rs", "research/private.jsonl",
                "src/invalid\x00.py", "src/journal.txt", "random/path.py",
            )
            for path in bad:
                with self.subTest(path=str(path)), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    audit(payload(sigs=[{"p": path, "r": 1}]), snap)

    def test_nonexisting_file_not_grounded(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            with self.assertRaises(shadow.ContextShadowError):
                audit(payload(sigs=[{"p": "src/ghost.rs", "r": 1}]), snap)

    def test_symlinked_output_file_denied(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            link = snap / "src" / "linked.rs"
            link.symlink_to(snap / "src" / "journal.rs")
            with self.assertRaises(shadow.ContextShadowError):
                audit(payload(sigs=[{"p": "src/linked.rs", "r": 1}]), snap)

    def test_empty_candidates_not_claimed_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            r = audit(payload(sigs=[]), snap)
            self.assertEqual(r["candidate_files"], [])
            self.assertFalse(r["independent_relevance_verified"])

    def test_more_than_twelve_paths_are_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            _, snap = fixture_dirs(Path(d))
            for i in range(24):
                (snap / "src" / f"f{i}.py").write_text("hello")
            items = [{"p": f"src/f{i}.py", "r": i + 1} for i in range(24)]
            r = audit(payload(sigs=items), snap)
            self.assertEqual(len(r["candidate_files"]), 12)

    def test_docker_command_is_no_network_no_pulls_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binpath = root / "ripwire"
            binpath.write_text("fake")
            snap = root / "code"
            snap.mkdir()
            args = shadow.docker_args(binpath, snap, TASK)
            joined = " ".join(args)
            for expected in (
                "--network=none", "--read-only", "--pull=never",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--no-cache", "--json", "--signatures-only",
                "timeout -k 2s 18s", "--token-budget=2400",
            ):
                self.assertIn(expected, joined)
            self.assertNotIn("--mcp", args)
            self.assertNotIn("--listen", args)
            self.assertNotIn("install", args)
            self.assertEqual(args[0:2], ["docker", "run"])

    def test_newline_and_oversize_task_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / "ripwire"
            binary.write_text("fake")
            snap = Path(d) / "code"
            snap.mkdir()
            for task in (None, "a", "x" * 241, "bad\nline", "bad\x00task"):
                with self.subTest(v=str(task)[:20]), self.assertRaises(
                    shadow.ContextShadowError
                ):
                    shadow.docker_args(binary, snap, task)

    def test_run_shadow_mocked_success(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            repo, _ = fixture_dirs(base)
            binary = base / "ripwire"
            binary.write_text("fake")
            def fake_docker(args, **kwargs):
                self.assertIn("--network=none", args)
                self.assertIn("--read-only", args)
                self.assertEqual(kwargs["timeout"], 30)
                return subprocess.CompletedProcess(args, 0, payload(), b"")
            with mock.patch.object(shadow, "verify_binary") as gate:
                with mock.patch.object(shadow.subprocess, "run", side_effect=fake_docker):
                    result = shadow.run_shadow(repo, binary, TASK)
            gate.assert_called_once()
            self.assertEqual(result["source_snapshot"]["files"], 2)
            self.assertEqual(result["production_gate"], "HOLD")
            self.assertEqual(result["candidate_files"][0]["first_symbol_rank"], 1)

    def test_subprocess_failure_never_leaks_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            repo, _ = fixture_dirs(base)
            binary = base / "ripwire"
            binary.write_text("fake")
            with mock.patch.object(shadow, "verify_binary"):
                with mock.patch.object(
                    shadow.subprocess, "run",
                    return_value=subprocess.CompletedProcess(
                        [], 1, b"", b"SECRET_CREDENTIAL"
                    ),
                ):
                    with self.assertRaises(shadow.ContextShadowError) as exc:
                        shadow.run_shadow(repo, binary, TASK)
            self.assertNotIn("SECRET_CREDENTIAL", str(exc.exception))

    def test_subprocess_timeout_is_bounded_failure(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            repo, _ = fixture_dirs(base)
            binary = base / "ripwire"
            binary.write_text("fake")
            with mock.patch.object(shadow, "verify_binary"):
                with mock.patch.object(
                    shadow.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(["docker"], 30),
                ):
                    with self.assertRaises(shadow.ContextShadowError):
                        shadow.run_shadow(repo, binary, TASK)

    def test_cli_refuses_unapproved_source_read(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = shadow.main([
                "--source-root", "/tmp/unknown",
                "--ripwire-binary", "/tmp/unknown/ripwire",
                "--task", TASK,
            ])
        self.assertEqual(rc, 2)
        self.assertIn("--allow-code-read", err.getvalue())

    def test_cli_prints_only_aggregate(self):
        expected = {
            "production_gate": "HOLD",
            "model_tokens_used": 0,
            "candidate_files": [],
        }
        result = io.StringIO()
        with mock.patch.object(shadow, "run_shadow", return_value=expected):
            with redirect_stdout(result):
                rc = shadow.main([
                    "--source-root", "/tmp/repo",
                    "--ripwire-binary", "/tmp/ripwire",
                    "--task", TASK,
                    "--allow-code-read",
                ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(result.getvalue()), expected)
        self.assertNotIn(TASK, result.getvalue())

    def test_cli_error_no_user_path_or_exception_leak(self):
        err = io.StringIO()
        with mock.patch.object(
            shadow, "run_shadow",
            side_effect=shadow.ContextShadowError("private")
        ):
            with redirect_stderr(err):
                rc = shadow.main([
                    "--source-root", "/tmp/secretcode",
                    "--ripwire-binary", "/tmp/key",
                    "--task", TASK, "--allow-code-read",
                ])
        self.assertEqual(rc, 3)
        self.assertNotIn("private", err.getvalue())
        self.assertNotIn("/tmp/secretcode", err.getvalue())

    def test_no_external_requests_or_live_trace_ingest(self):
        src = SOURCE.read_text()
        for forbidden in (
            "import requests", "import urllib", "socket.connect(",
            "os.system(", "shell=True", "--mcp", "--listen",
            "private_traces", "openai.ChatCompletion", "Anthropic(",
        ):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
