"""Synthetic unit tests for bounded metadata-only lineage analysis."""
import json
from pathlib import Path
import tempfile
import unittest
from source_history_coverage import range_coverage,summarize,inventory


class IntervalTests(unittest.TestCase):
    def test_overlap_no_internal_gap(self):
        v=range_coverage([(0,100,"01"),(30,70,"02"),(50,130,"03")])
        self.assertEqual(v["adjacent_overlaps"],2)
        self.assertEqual(v["internal_uncovered_ranges"],0)
        self.assertFalse(v["initial_uncovered_prefix"])

    def test_positive_gap_detected(self):
        v=range_coverage([(0,10,"01"),(20,30,"02")])
        self.assertEqual(v["adjacent_forward_gaps"],1)
        self.assertEqual(v["internal_uncovered_ranges"],1)

    def test_initial_missing_prefix_not_total_gap(self):
        v=range_coverage([(7,20,"01"),(19,40,"02")])
        self.assertTrue(v["initial_uncovered_prefix"])
        self.assertEqual(v["internal_uncovered_ranges"],0)

    def test_input_rejects_boolean_offset(self):
        v=range_coverage([(True,15,"01"),(0,30,"02")])
        self.assertEqual(v["malformed"],1)

    def test_timestamp_reset_is_not_positive_union_gap(self):
        v=range_coverage([(0,90,"01"),(0,60,"02"),(60,100,"03")])
        self.assertEqual(v["adjacent_overlaps"],1)
        self.assertEqual(v["internal_uncovered_ranges"],0)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.state={"sources":{
            "runtime:/path/nonempty":{"offset":12},
            "runtime:/path/empty":{"offset":0},
            "dc:/path/archived":{"offset":20,"source_dev":1,
                                 "source_inode":2,"prefix_sha256":"a"*64}}}
        self.manifests=[{"stamp_utc":"2026-10-09T13:00:00Z","files":[
            {"source":"/path/archived","source_group":"dc",
             "offset_from":0,"size_at_open":20}]}]

    def test_source_groups_and_legacy(self):
        obj=summarize(self.state,self.manifests)
        self.assertEqual(obj["group_summary"]["runtime"]["unmatched_nonzero_offset"],1)
        self.assertEqual(obj["group_summary"]["runtime"]["unmatched_zero_offset"],1)
        self.assertEqual(obj["group_summary"]["dc"]["matched_local_manifest"],1)
        self.assertFalse(obj["migration_approved"])
        self.assertNotIn("/path/",json.dumps(obj))

    def test_manifest_malformed_does_not_assert_coverage(self):
        bad=[{"stamp_utc":"stamp","files":[
          {"source":"/path/archived","source_group":"dc",
           "offset_from":0,"size_at_open":True}]}]
        obj=summarize(self.state,bad)
        self.assertEqual(obj["metadata_diagnostics"]["incomplete_legacy_interval"],1)
        self.assertEqual(obj["group_summary"]["dc"].get("matched_local_manifest",0),0)

    def test_two_archive_claims_overlap(self):
        added={"stamp_utc":"2026-10-09T14:00:00Z","files":[{
           "source":"/path/archived","source_group":"dc",
           "offset_from":5,"size_at_open":30}]}
        obj=summarize(self.state,self.manifests+[added])
        self.assertEqual(obj["coverage"]["adjacent_overlap_or_reset_transitions"],1)
        self.assertEqual(obj["coverage"]["internal_uncovered_ranges_after_union"],0)

    def test_negative_checkpoint_offset_is_not_accepted(self):
        broken={"sources":{"dc:/path/negative":{"offset":-1}}}
        result=summarize(broken,[])
        self.assertEqual(result["coverage"]["invalid_cursor_record"],1)
        self.assertEqual(result["group_summary"]["dc"]["cursors"],1)
        self.assertNotIn("unmatched_zero_offset",result["group_summary"]["dc"])

    def test_bounded_spool_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"sealed").mkdir()
            (root/"state").mkdir()
            (root/"state"/"offsets.json").write_text(json.dumps(self.state))
            (root/"sealed"/"synthetic.tar.zst.manifest.json").write_text(
                json.dumps(self.manifests[0]))
            (root/"sealed"/"synthetic.tar.zst").write_bytes(b"fixture")
            (root/"sealed"/"synthetic.tar.zst.ready").write_text("synthetic")
            obj=inventory(root)
            self.assertEqual(obj["checkpoint_entries"],3)
            self.assertEqual(obj["manifest_count"],1)

    def test_rejects_incomplete_local_seal_triplet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"sealed").mkdir()
            (root/"state").mkdir()
            (root/"state"/"offsets.json").write_text(json.dumps(self.state))
            (root/"sealed"/"synthetic.tar.zst.manifest.json").write_text(
                json.dumps(self.manifests[0]))
            with self.assertRaisesRegex(ValueError,"incomplete local sealed"):
                inventory(root)

    def test_rejects_metadata_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"sealed").mkdir()
            (root/"state").mkdir()
            data=root/"data.json";data.write_text(json.dumps(self.state))
            (root/"state"/"offsets.json").symlink_to(data)
            with self.assertRaises(ValueError):
                inventory(root)


if __name__=="__main__":
    unittest.main(verbosity=2)