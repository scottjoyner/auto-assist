"""Synthetic tests for the read-only orphan integrity verifier."""
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import orphan_integrity_review as review


class OrphanIntegrityReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="issue118-orphan-fixture-")
        self.addCleanup(self.tmp.cleanup)
        root=pathlib.Path(self.tmp.name)
        data=b"synthetic-compressed-fixture-bytes"
        name="fixture-"+hashlib.sha256(data).hexdigest()[:8]+".tar.zst"
        self.archive=root/name
        self.archive.write_bytes(data)
        self.payload=b'{"fake":true}\n'
        self.rows=[{"basename":"sanitized.jsonl","sha256":hashlib.sha256(self.payload).hexdigest(),
                    "bytes_out":len(self.payload),"source_group":"fiction","source":"synthetic"}]
        self.manifest={"schemaVersion":1,"files":self.rows,"databases":[]}
        self.fake=lambda _path,part,max_bytes=review.MAX_MEMBER_BYTES:(
            json.dumps(self.manifest).encode() if part=="manifest.json" else
            self.payload if part=="sanitized.jsonl" else b""
        )

    def test_valid_orphan_no_live_writes(self):
        with mock.patch.object(review,"read_member",side_effect=self.fake):
            receipt=review.verify_archive(self.archive)
        self.assertEqual(receipt["sha256_and_size_verified_members"],1)
        self.assertEqual(receipt["live_sidecar_created"],False)
        self.assertEqual(receipt["archive_bytes_written"],0)

    def test_deterministic_external_sidecar_control(self):
        digest=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        external=dict(self.manifest,archive=self.archive.name,
                      archive_bytes=self.archive.stat().st_size,archive_sha256=digest)
        sidecar=self.archive.with_suffix(".manifest.json")
        sidecar.write_text(json.dumps(external))
        with mock.patch.object(review,"read_member",side_effect=self.fake):
            receipt=review.verify_archive(self.archive,control_manifest=sidecar,verify_members=False)
        self.assertTrue(receipt["reconstructed_sidecar_matches_control"])
        self.assertFalse(receipt["source_members_verified"])

    def test_mismatched_archive_member_fails(self):
        self.rows[0]["sha256"]="0"*64
        with mock.patch.object(review,"read_member",side_effect=self.fake):
            with self.assertRaises(ValueError):
                review.verify_archive(self.archive)

    def test_path_traversal_member_fails(self):
        self.rows[0]["basename"]="../untrusted"
        with mock.patch.object(review,"read_member",side_effect=self.fake):
            with self.assertRaises(ValueError):
                review.verify_archive(self.archive)

    def test_wrong_archive_name_digest_fails(self):
        wrong=self.archive.with_name("fixture-wrong000.tar.zst")
        wrong.write_bytes(self.archive.read_bytes())
        with self.assertRaises(ValueError):
            review.verify_archive(wrong)


if __name__=="__main__":
    unittest.main(verbosity=2)