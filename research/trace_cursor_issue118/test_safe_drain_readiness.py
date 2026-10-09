"""Synthetic safety acceptance for bounded drain readiness gating."""
import json
from pathlib import Path
import tempfile
import unittest
from safe_drain_readiness import assess,run


def fake():
    safe={"verdict":"held_admission","pending_files":257,
          "automatic_cleanup_permitted":False}
    watch={"drainBacklogPendingAck":260,"drainRetainedAcked":173,
           "drainBacklogRaw":433,"state":"warn"}
    archive={"state":"problems","sha256Verified":60,"archivedSegments":1002,
             "checkedUtc":"2026-10-09T08:36:58Z","captureHoles":[{"gapMinutes":24}]}
    coverage={"groups":{
        "runtime_traces":{"unmatched_nonzero_offset":134},
        "dc_traces":{"unmatched_nonzero_offset":2},
        "zcode_traces":{"unmatched_nonzero_offset":7},
        "console_events":{"unmatched_nonzero_offset":1},
        "token_usage":{"unmatched_nonzero_offset":0}
    },"unmatched_nonzero_without_archive_group_evidence":134}
    return safe,watch,archive,coverage


class ReadinessTests(unittest.TestCase):
    def test_held_admission_never_authorizes_release(self):
        data=assess(*fake())
        self.assertEqual(data["gate"],"HOLD")
        self.assertFalse(data["automatic_source_release_allowed"])
        self.assertFalse(data["archive_deletion_authorized"])
        self.assertFalse(data["production_migration_authorized"])

    def test_retained_acked_not_counted_as_pending(self):
        data=assess(*fake())
        self.assertEqual(data["watchdog_pending_ack"],260)
        self.assertEqual(data["watchdog_retained_acked"],173)
        self.assertEqual(data["watchdog_raw_backlog"],433)
        self.assertEqual(data["safe_drain_pending_files"],257)

    def test_unmatched_history_stays_separate(self):
        data=assess(*fake())
        self.assertEqual(data["unmatched_nonzero_source_cursors"],144)
        self.assertEqual(data["unmatched_without_cached_group_evidence"],134)
        self.assertIn("SOURCE_HISTORY_NOT_RECONCILED",data["reasons"])

    def test_no_paths_or_nas_io_claims(self):
        data=assess(*fake())
        self.assertFalse(data["nas_access_performed"])
        self.assertFalse(data["source_path_output"])
        self.assertNotIn("/nas/",json.dumps(data))

    def test_false_counters_denied(self):
        safe,watch,archive,coverage=fake()
        safe["pending_files"]=True
        with self.assertRaises(ValueError):
            assess(safe,watch,archive,coverage)

    def test_partition_inconsistency_denied(self):
        safe,watch,archive,coverage=fake()
        watch["drainBacklogRaw"]=100
        with self.assertRaises(ValueError):
            assess(safe,watch,archive,coverage)

    def test_sha_overclaim_denied(self):
        safe,watch,archive,coverage=fake()
        archive["sha256Verified"]=1003
        with self.assertRaises(ValueError):
            assess(safe,watch,archive,coverage)

    def test_no_independent_prod_authority_even_when_all_green(self):
        safe,watch,archive,coverage=fake()
        safe.update(verdict="ok",pending_files=0)
        watch.update(drainBacklogRaw=0,drainBacklogPendingAck=0,
                     drainRetainedAcked=0,state="ok")
        archive.update(state="ok",sha256Verified=1002,captureHoles=[])
        for x in coverage["groups"].values():x["unmatched_nonzero_offset"]=0
        coverage["unmatched_nonzero_without_archive_group_evidence"]=0
        data=assess(safe,watch,archive,coverage)
        self.assertEqual(data["gate"],"REVIEW_ONLY")
        self.assertFalse(data["production_migration_authorized"])

    def test_run_is_local_metadata_only(self):
        safe,watch,archive,coverage=fake()
        with tempfile.TemporaryDirectory(prefix="issue118-readiness-") as td:
            path=Path(td)
            for name,doc in [
                ("safe-drain-status.json",safe),
                ("watchdog.json",watch),
                ("archive-inventory.json",archive),
                ("coverage.json",coverage)
            ]:
                (path/name).write_text(json.dumps(doc))
            self.assertEqual(run(path,path/"coverage.json")["gate"],"HOLD")

    def test_symlink_status_rejected(self):
        safe,watch,archive,coverage=fake()
        with tempfile.TemporaryDirectory(prefix="issue118-readiness-") as td:
            path=Path(td)
            for name,doc in [
                ("safe-drain-status-original.json",safe),
                ("watchdog.json",watch),
                ("archive-inventory.json",archive),
                ("coverage.json",coverage)
            ]:
                (path/name).write_text(json.dumps(doc))
            (path/"safe-drain-status.json").symlink_to(path/"safe-drain-status-original.json")
            with self.assertRaises(ValueError):
                run(path,path/"coverage.json")


if __name__=="__main__":
    unittest.main(verbosity=2)

