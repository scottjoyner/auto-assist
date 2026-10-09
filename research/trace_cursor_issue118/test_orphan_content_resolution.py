"""Synthetic content resolution tests. No production database or archive access."""
import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock
import orphan_content_resolution as witness

class ContentResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="issue118-refs-")
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        db=sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE synthetic(value INTEGER)")
            db.execute("INSERT INTO synthetic VALUES(3)")
            self.db_data=db.serialize()
        finally:
            db.close()
        self.digest=hashlib.sha256(self.db_data).hexdigest()
        self.physical_item={"basename":"synthetic.db","sha256":self.digest,
                            "stored_bytes":len(self.db_data)}
        self.embedded_name="synthetic-parent-abcdef12.tar.zst"
        self.parent=self.root/self.embedded_name
        self.parent.write_bytes(b"fictional-archive-placeholder")

    def test_physical_sqlite_verified(self):
        with mock.patch.object(witness,"extract",return_value=self.db_data):
            r=witness.check_physical(self.parent,self.physical_item)
        self.assertTrue(all(r[k] for k in ("size_match","sha256_match","quick_check")))

    def test_corrupt_content_denied(self):
        bad=self.db_data[:-1]+b"X"
        with mock.patch.object(witness,"extract",return_value=bad):
            r=witness.check_physical(self.parent,self.physical_item)
        self.assertFalse(r["sha256_match"])

    def test_fake_sha_receipt_denied(self):
        item={**self.physical_item,"sha256":"0"*64}
        with mock.patch.object(witness,"extract",return_value=self.db_data):
            r=witness.check_physical(self.parent,item)
        self.assertFalse(r["sha256_match"])

    def test_bad_schema_denied(self):
        item={**self.physical_item,"stored_bytes":True}
        with mock.patch.object(witness,"extract",return_value=self.db_data):
            with self.assertRaises(ValueError):
                witness.check_physical(self.parent,item)

    def test_reference_to_locally_stored_db_resolves(self):
        child=self.root/"fictional-child-abcdef12.tar.zst"
        child.write_bytes(b"fictional-child-archive")
        (self.root/(self.embedded_name+".manifest.json")).write_text(
            json.dumps({"databases":[self.physical_item]}))
        orphan_row={"sha256":self.digest,"reference_segment":"synthetic-parent"}
        def read(archive,filename):
            if archive==child and filename=="manifest.json":
                return json.dumps({"databases":[orphan_row]}).encode()
            if archive==self.parent and filename=="synthetic.db":
                return self.db_data
            raise ValueError("unknown synthetic request")
        with mock.patch.object(witness,"extract",side_effect=read):
            result=witness.run(self.root)
        self.assertTrue(result["all_content_restorable"])
        self.assertEqual(result["counts"]["reference_unique_name_pointer"],1)

    def test_reference_without_content_fails_closed(self):
        child=self.root/"fictional-child-abcdef12.tar.zst"
        child.write_bytes(b"fictional-child-archive")
        item={"sha256":self.digest,"reference_segment":"missing-parent"}
        with mock.patch.object(witness,"extract",return_value=json.dumps(
            {"databases":[item]}).encode()):
            result=witness.run(self.root)
        self.assertFalse(result["all_content_restorable"])
        self.assertEqual(result["counts"]["physical_unique_digests_missing"],1)

if __name__=="__main__":
    unittest.main(verbosity=2)

