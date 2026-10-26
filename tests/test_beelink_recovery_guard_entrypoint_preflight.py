"""No-network, no-root preflight regressions for Beelink guard entrypoint."""
from __future__ import annotations
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SRC=Path(__file__).resolve().parents[1]/"scripts"/"beelink_recovery_guard_entrypoint_preflight.py"
spec=importlib.util.spec_from_file_location("beelink_guard_preflight",SRC)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class TestGuard(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="guard-entrypoint-test-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/mod.SCRIPT
        self.path.write_text("# synthetic; not executed\n")
        self.path.chmod(0o600)

    def show(self, cwd=None, arg="./recovery-memory-guard.py", exe="/usr/bin/python3"):
        cwd=self.root if cwd is None else cwd
        return ("WorkingDirectory="+str(cwd)+"\n"
                "ExecStart={ path="+exe+" ; argv[]="+exe+" -u "+arg+
                " ; ignore_errors=no ; start_time=[n/a] }\nUser=root\n")

    def verdict(self, data=None):
        return mod.inspect_unit(self.show() if data is None else data)

    def hold(self, reason, data=None):
        x=self.verdict(data)
        self.assertEqual(x["verdict"],"HOLD")
        self.assertEqual(x["reason"],reason)
        self.assertFalse(x["guard_executed"])
        self.assertFalse(x["service_restarted"])
        self.assertFalse(x["deployment_authorized"])
        self.assertNotIn(str(self.root),str(x))

    def test_present_is_not_healthy_guard_proof(self):
        x=self.verdict()
        self.assertEqual(x["verdict"],"ENTRYPOINT_METADATA_ONLY")
        self.assertFalse(x["memory_admission_proven"])
        self.assertFalse(x["source_commit_verified"])
        self.assertFalse(x["privileged_access_performed"])

    def test_missing_script(self):
        self.path.unlink()
        self.hold("ENTRYPOINT_PATH_MISSING")

    def test_script_directory(self):
        self.path.unlink();self.path.mkdir()
        self.hold("ENTRYPOINT_NOT_REGULAR_FILE")

    def test_world_writable_script(self):
        self.path.chmod(0o666)
        self.hold("ENTRYPOINT_GROUP_OR_WORLD_WRITABLE")

    def test_symlinked_script(self):
        self.path.rename(self.root/"real.py")
        self.path.symlink_to(self.root/"real.py")
        self.hold("SYMLINKED_ENTRYPOINT_PATH")

    def test_symlinked_parent(self):
        other=self.root.with_name(self.root.name+"-else")
        self.root.rename(other);self.root.symlink_to(other,target_is_directory=True)
        try:
            self.hold("SYMLINKED_ENTRYPOINT_PATH")
        finally:
            self.root.unlink();other.rename(self.root)

    def test_relative_traversal_refused(self):
        self.hold("parent_traversal_refused",self.show(arg="../recovery-memory-guard.py"))

    def test_unqualified_working_directory(self):
        self.hold("invalid_working_directory",self.show(cwd="relative/path"))

    def test_missing_argv(self):
        self.hold("argv_unavailable","WorkingDirectory="+str(self.root)+"\nExecStart={ path=/usr/bin/python3 }")

    def test_unexpected_launcher(self):
        self.hold("unexpected_launcher",self.show(exe="/bin/bash"))

    def test_unexpected_script_name(self):
        self.hold("unexpected_script_identity",self.show(arg="./not-the-guard.py"))

    def test_duplicate_property(self):
        self.hold("duplicate_unit_property",self.show()+"WorkingDirectory=/else\n")

    def test_oversize_unit(self):
        self.hold("invalid_unit_properties","x"*(mod.MAX_UNIT_BYTES+1))

    def test_missing_script_reason_without_full_path(self):
        self.path.unlink()
        x=self.verdict()
        self.assertNotIn(str(self.root),str(x))
        self.assertEqual(x["script_basename"],mod.SCRIPT)

    def test_parser_does_not_accept_renamed_file(self):
        self.path.rename(self.root/"different.py")
        self.hold("ENTRYPOINT_PATH_MISSING")

    def test_cli_does_not_run_unit_or_print_paths(self):
        import subprocess,json
        mock=subprocess.CompletedProcess(args=[],returncode=0,stdout=self.show(),stderr="")
        with patch.object(mod.subprocess,"run",return_value=mock) as proc, \
             patch.object(sys,"argv",["guard-preflight"]) , \
             patch("sys.stdout",new_callable=io.StringIO) as output:
            self.assertEqual(mod.main(),0)
        args=proc.call_args.args[0]
        self.assertEqual(args[:3],["systemctl","show",mod.UNIT])
        result=json.loads(output.getvalue())
        self.assertFalse(result["guard_executed"])
        self.assertNotIn(str(self.root),output.getvalue())

if __name__=="__main__":
    unittest.main()
