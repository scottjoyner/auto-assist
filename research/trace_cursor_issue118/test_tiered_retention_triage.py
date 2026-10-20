"""Synthetic acceptance for issue #118 tiered, nonauthoritative custody triage."""
import copy
import pathlib
import tempfile
import json
import unittest
from tiered_retention_triage import triage, run


def sample():
    local={"group_summary":{
        "runtime_traces":{"cursors":5,"matched_local_manifest":1,
                          "unmatched_nonzero_offset":4,
                          "legacy_without_provenance":5},
        "zcode_traces":{"cursors":3,"unmatched_nonzero_offset":3,
                        "legacy_without_provenance":3},
        "token_usage":{"cursors":1,"matched_local_manifest":1,
                       "legacy_without_provenance":1}
    }}
    cached={"checkedUtc":"2026-10-09T08:36:58Z","state":"problems",
       "archivedSegments":20,"inboxSegments":4,"sha256Verified":3,
       "deepVerifiedThisRun":1,
       "sourceGroupCoverage":{"zcode_traces":8},
       "groupsNeverSeen":["runtime_traces","token_usage"],
       "captureHoles":[{"gapMinutes":12}]}
    return local,cached


class TieredTriageTests(unittest.TestCase):
    def test_classifies_missing_group_and_only_group_level(self):
        local,cached=sample()
        obj=triage(local,cached)
        self.assertEqual(obj["unmatched_nonzero_without_archive_group_evidence"],4)
        self.assertEqual(obj["unmatched_nonzero_with_archive_group_only_evidence"],3)
        self.assertEqual(obj["groups"]["runtime_traces"]["archive_inventory_status"],
                         "NOT_SEEN_IN_CACHED_ARCHIVE_GROUP_INDEX")
        self.assertEqual(obj["groups"]["zcode_traces"]["archive_inventory_status"],
                         "GROUP_PRESENT_ONLY_SOURCE_LINEAGE_UNPROVEN")

    def test_no_source_paths_or_migration_proof(self):
        local,cached=sample()
        obj=triage(local,cached)
        self.assertFalse(obj["migration_authorized"])
        self.assertEqual(obj["source_bytes_opened"],0)
        self.assertEqual(obj["archive_payload_bytes_opened"],0)
        self.assertFalse(obj["inventory_is_live_proof"])
        self.assertNotIn("/home/",json.dumps(obj))

    def test_rejects_contradictory_group_coverage(self):
        local,cached=sample()
        cached["sourceGroupCoverage"]["runtime_traces"]=1
        with self.assertRaises(ValueError):
            triage(local,cached)

    def test_invalid_boolean_count_rejected(self):
        local,cached=sample()
        local["group_summary"]["runtime_traces"]["cursors"]=True
        with self.assertRaises(ValueError):
            triage(local,cached)

    def test_impossible_source_counts_rejected(self):
        local,cached=sample()
        local["group_summary"]["runtime_traces"]["unmatched_nonzero_offset"]=10
        with self.assertRaises(ValueError):
            triage(local,cached)

    def test_impossible_verification_count_rejected(self):
        local,cached=sample()
        cached["sha256Verified"]=100
        with self.assertRaises(ValueError):
            triage(local,cached)

    def test_invalid_never_seen_group_rejected(self):
        local,cached=sample()
        cached["groupsNeverSeen"]=[{"bad":"x"}]
        with self.assertRaises(ValueError):
            triage(local,cached)

    def test_works_with_bounded_synthetic_files(self):
        local,cached=sample()
        with tempfile.TemporaryDirectory() as td:
            a,b=pathlib.Path(td)/"a.json",pathlib.Path(td)/"b.json"
            a.write_text(json.dumps(local));b.write_text(json.dumps(cached))
            self.assertEqual(run(a,b)["cached_capture_holes"],1)

    def test_rejects_symlink_archive_input(self):
        local,cached=sample()
        with tempfile.TemporaryDirectory() as td:
            a,b,c=[pathlib.Path(td)/x for x in ("a.json","b.json","c.json")]
            a.write_text(json.dumps(local));b.write_text(json.dumps(cached));c.symlink_to(b)
            with self.assertRaises(ValueError):
                run(a,c)


if __name__=="__main__":
    unittest.main(verbosity=2)

