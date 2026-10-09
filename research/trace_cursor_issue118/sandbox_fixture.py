"""Hermetic integration replay of the real local-first collector source code.

Imports only the CODE of /home/scott/bin/trace-spool-capture.py.  All runtime
paths and bytes used by capture() are generated inside a TemporaryDirectory.
The corrected variant is transformed in memory; it is never installed.
"""
import hashlib
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

COLLECTOR=Path(__file__).with_name("baseline-trace-spool-capture.py")
BASELINE_SLICE='''            data = data[prev:] if prev <= len(data) else data
            if not data:
                continue
'''
FIXED_SLICE='''            # Trimmed prefix is authoritative; do not commit an unfinished line.
            committed_end = len(data)
            if prev > committed_end:
                failures.append(f"{key}: cursor beyond last complete line; review source generation")
                continue
            data = data[prev:committed_end]
            if not data:
                continue
'''
BASELINE_CURSOR='''            offsets["sources"][key] = {"offset": size_at_open, "updated": stamp}'''
FIXED_CURSOR='''            offsets["sources"][key] = {"offset": committed_end, "updated": stamp}'''
BASELINE_META='''                "size_at_open": size_at_open,
                "torn_tail_dropped": torn,
'''
FIXED_META='''                "size_at_open": size_at_open,
                "offset_end_committed": committed_end,
                "torn_tail_dropped": torn,
'''


def load_collector(candidate=False):
    """Use source-code-only input; all model/agent/NAS data is never opened."""
    src=COLLECTOR.read_text(encoding="utf-8")
    if candidate:
        for old,new in ((BASELINE_SLICE,FIXED_SLICE),
                        (BASELINE_CURSOR,FIXED_CURSOR),
                        (BASELINE_META,FIXED_META)):
            if src.count(old)!=1:
                raise AssertionError("Owner source changed: freeze and re-review patch")
            src=src.replace(old,new)
    m=types.ModuleType("synthetic_collector_candidate" if candidate
                       else "synthetic_collector_original")
    m.__file__=str(COLLECTOR)
    exec(compile(src,str(COLLECTOR),"exec"),m.__dict__)
    return m


class CollectorSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="gliner08_")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.dc_hosted=self.root/"hosted"/".claude-server-commander"
        self.dc_isolated=self.root/"dc-isolated"/".home"/".claude-server-commander"
        self.dc_hosted.mkdir(parents=True)
        self.dc_isolated.mkdir(parents=True)
        self.spool=self.root/"spool"
        self.app=self.root/"app"
        self.app.mkdir()
        self.trace=self.dc_hosted/"tool-history.jsonl"
        self.args=SimpleNamespace(spool=str(self.spool),app_root=str(self.app),
                                  dc_dir=[str(self.dc_hosted),str(self.dc_isolated)],
                                  zcode_dir="")
        self._n=0

    def module(self,candidate):
        m=load_collector(candidate)
        # Deterministic unique timestamps so captures run without real-clock waits.
        def stamp():
            self._n+=1
            return f"20261008T12{self._n:04d}Z"
        m.utc_stamp=stamp
        self.assertTrue(all(
            str(path).startswith(str(self.root)+"/")
            for entries in m.enumerate_sources(
                self.app,[self.dc_hosted,self.dc_isolated],self.spool/"console",None
            ).values()
            for path in entries
        ))
        return m

    def manifests(self):
        return sorted((self.spool/"sealed").glob("*.tar.zst.manifest.json"))

    def source_cursor(self):
        j=json.loads((self.spool/"state"/"offsets.json").read_text())
        return j["sources"][f"dc_traces:{self.trace}"]["offset"]

    def fixture_line(self, marker):
        # Entirely invented test text (no real arguments / logs).
        return json.dumps({"sandbox_case_id":marker,"status":"ok"},
                          separators=(",",":")).encode()+b"\n"

    def archive_text(self,manifest_path):
        j=json.loads(manifest_path.read_text())
        archive=manifest_path.with_name(j["archive"])
        name="./dc_traces__tool-history.jsonl"
        out=subprocess.run(["tar","-I","zstd","-xOf",str(archive),name],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           check=True,timeout=20)
        return out.stdout

    def test_original_collector_demonstrates_record_prefix_loss(self):
        original_sha=hashlib.sha256(COLLECTOR.read_bytes()).digest()
        m=self.module(False)
        a,b=self.fixture_line("FIRST"),self.fixture_line("SECOND")
        split=len(b)//2
        self.trace.write_bytes(a+b[:split])
        self.assertEqual(m.capture(self.args),0)
        initial=self.manifests()
        self.assertEqual(len(initial),1)
        self.assertEqual(self.archive_text(initial[0]),a)
        self.assertEqual(self.source_cursor(),len(a)+split)
        self.trace.write_bytes(a+b)
        self.assertEqual(m.capture(self.args),0)
        results=b"".join(self.archive_text(p) for p in self.manifests())
        self.assertNotEqual(results,a+b)
        self.assertNotIn(b'"sandbox_case_id":"SECOND"',results)
        self.assertEqual(hashlib.sha256(COLLECTOR.read_bytes()).digest(),
                         original_sha)

    def test_fixed_in_memory_collector_preserves_two_appends_and_restart(self):
        original_sha=hashlib.sha256(COLLECTOR.read_bytes()).digest()
        m=self.module(True)
        a,b=self.fixture_line("FIRST"),self.fixture_line("SECOND")
        partial=len(b)//2
        self.trace.write_bytes(a+b[:partial])
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(self.source_cursor(),len(a))
        first=self.manifests()
        self.assertEqual(len(first),1)
        metadata=json.loads(first[0].read_text())["files"][0]
        self.assertEqual(metadata["offset_end_committed"],len(a))
        self.assertEqual(metadata["size_at_open"],len(a)+partial)
        # Simulate collector restart: reload source code, preserve spool offsets.
        m2=self.module(True)
        self.trace.write_bytes(a+b)
        self.assertEqual(m2.capture(self.args),0)
        self.assertEqual(self.source_cursor(),len(a+b))
        saved=self.manifests()
        self.assertEqual(len(saved),2)
        self.assertEqual(b"".join(self.archive_text(p) for p in saved),a+b)
        self.assertEqual(hashlib.sha256(COLLECTOR.read_bytes()).digest(),
                         original_sha)

    def test_fixed_zero_newline_then_completion(self):
        m=self.module(True)
        full=self.fixture_line("ONE")
        self.trace.write_bytes(full[:-1])
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(len(self.manifests()),0)
        # No byte cursor should be committed because there was no complete line.
        self.assertFalse((self.spool/"state"/"offsets.json").exists())
        self.trace.write_bytes(full)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(self.source_cursor(),len(full))
        self.assertEqual(self.archive_text(self.manifests()[0]),full)

    def test_two_source_groups_keep_independent_cursors(self):
        m=self.module(True)
        hosted=self.fixture_line("HOSTED")
        isolated=self.fixture_line("ISOLATED")
        isolated_trace=self.dc_isolated/"tool-history.jsonl"
        self.trace.write_bytes(hosted)
        isolated_trace.write_bytes(isolated)
        self.assertEqual(m.capture(self.args),0)
        j=json.loads(self.manifests()[0].read_text())
        dc={x["source_group"]:x for x in j["files"] if x.get("source_group","").startswith("dc_traces")}
        self.assertEqual(set(dc),{"dc_traces","dc_traces_selfhosted"})
        self.assertEqual(dc["dc_traces"]["offset_end_committed"],len(hosted))
        self.assertEqual(dc["dc_traces_selfhosted"]["offset_end_committed"],len(isolated))
        # Hosted stopped; isolated appended and is still independently captured.
        isolated_extra=self.fixture_line("ISOLATED_NEXT")
        isolated_trace.write_bytes(isolated+isolated_extra)
        self.assertEqual(m.capture(self.args),0)
        j2=json.loads(self.manifests()[1].read_text())
        self.assertEqual([x["source_group"] for x in j2["files"]],["dc_traces_selfhosted"])
        out=subprocess.run(["tar","-I","zstd","-xOf",
                  str(self.manifests()[1].with_name(j2["archive"])),
                  "./dc_traces_selfhosted__tool-history.jsonl"],
                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20)
        self.assertEqual(out.stdout,isolated_extra)

    def test_no_new_append_emits_no_duplicate_seal(self):
        m=self.module(True)
        payload=self.fixture_line("ONCE")
        self.trace.write_bytes(payload)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(len(self.manifests()),1)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(len(self.manifests()),1)

    def test_source_shrink_current_behavior_restarts_at_zero_without_generation(self):
        m=self.module(True)
        original=self.fixture_line("LONG_LONG_LONG_MARKER")
        short=self.fixture_line("SHORT")
        self.assertLess(len(short),len(original))
        self.trace.write_bytes(original)
        self.assertEqual(m.capture(self.args),0)
        self.trace.write_bytes(short)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(self.archive_text(self.manifests()[-1]),short)
        j=json.loads(self.manifests()[-1].read_text())
        self.assertTrue(any("shrank" in note for note in j.get("notes",[])))
        # This shows generation/reset is recorded as a note, not authenticated.

    def test_unicode_codepoint_split_across_source_appends(self):
        m=self.module(True)
        first=self.fixture_line("FIRST")
        second=json.dumps({"sandbox":"unicode✨"},ensure_ascii=False,separators=(",",":")).encode()+b"\n"
        split=second.index("✨".encode())+1
        self.trace.write_bytes(first+second[:split])
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(self.source_cursor(),len(first))
        self.trace.write_bytes(first+second)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(b"".join(self.archive_text(f) for f in self.manifests()),first+second)

    def test_same_size_replacement_is_an_open_generation_gap(self):
        m=self.module(True)
        a,b=self.fixture_line("AA"),self.fixture_line("BB")
        self.assertEqual(len(a),len(b))
        self.trace.write_bytes(a)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(len(self.manifests()),1)
        self.trace.write_bytes(b)
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(len(self.manifests()),1)
        # The minimal cursor fix is NOT an inode/generation fix.
        self.assertEqual(self.archive_text(self.manifests()[0]),a)

    def test_seal_before_cursor_persist_can_duplicate_on_retry(self):
        m=self.module(True)
        a=self.fixture_line("RETRY")
        self.trace.write_bytes(a)
        orig=m.Spool.save_offsets
        def fail_once(spool,off):
            raise OSError("synthetic pre-checkpoint failure")
        m.Spool.save_offsets=fail_once
        try:
            with self.assertRaisesRegex(OSError,"synthetic pre-checkpoint"):
                m.capture(self.args)
        finally:
            m.Spool.save_offsets=orig
        self.assertEqual(len(self.manifests()),1)
        self.assertFalse((self.spool/"state"/"offsets.json").exists())
        self.assertEqual(m.capture(self.args),0)
        self.assertEqual(len(self.manifests()),2)
        data=b"".join(self.archive_text(p) for p in self.manifests())
        self.assertEqual(data,a+a)  # exactly-once semantics NOT guaranteed


if __name__=="__main__":
    unittest.main()