"""Synthetic, no-NAS tests of bounded issue #118 legacy ACK receipt census."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import audit_legacy_ack_remote as audit


class LegacyAckAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="issue118-audit-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.name="trc-x1-370-20261009T160000Z-1234abcd.tar.zst"
        self.arch=self.root/self.name
        self.payload=b"synthetic archive fixture, no real trace bytes"
        self.arch.write_bytes(self.payload)
        self.sha=hashlib.sha256(self.payload).hexdigest()
        manifest=b'{"files":[],"schema":1}\n'
        (self.root/(self.name+".manifest.json")).write_bytes(manifest)
        (self.root/(self.name+".ready")).write_text(self.sha+"\n")
        ack={"schema":"safe-trace-nas-ack/v1","sha256":self.sha,
             "bytes":len(self.payload),"manifest_sha256":hashlib.sha256(manifest).hexdigest(),
             "beelink_uuid":audit.EXPECTED_UUID,
             "nas_remote_path":audit.LOCAL_PREFIX+self.name,
             "source_release":"BLOCKED"}
        self.ack=self.root/(self.name+".ack.json")
        self.ack.write_text(json.dumps(ack))
        self.record=ack

    def test_valid_legacy_ack_is_not_custody_attestation(self):
        counts,rows=audit.local_census(self.root)
        self.assertEqual(counts["local_provenance_structurally_valid"],1)
        self.assertEqual(counts["legacy_ack_missing_ready_sha256"],1)
        self.assertEqual(len(rows),1)
        self.assertNotIn(str(self.root),json.dumps(dict(counts)))

    def test_missing_ready_is_not_eligible(self):
        (self.root/(self.name+".ready")).unlink()
        counts,rows=audit.local_census(self.root)
        self.assertEqual(counts["local_invalid_or_untrusted"],1)
        self.assertEqual(rows,[])

    def test_altered_local_manifest_is_rejected(self):
        (self.root/(self.name+".manifest.json")).write_text('{"diverged":true}\n')
        counts,rows=audit.local_census(self.root)
        self.assertEqual(counts["local_invalid_or_untrusted"],1)
        self.assertEqual(rows,[])

    def test_wrong_source_size_is_rejected(self):
        self.arch.write_bytes(self.payload+b"extra")
        counts,rows=audit.local_census(self.root)
        self.assertEqual(counts["local_invalid_or_untrusted"],1)
        self.assertEqual(rows,[])

    def test_release_unblocked_metadata_is_rejected(self):
        self.record["source_release"]="ALLOWED"
        self.ack.write_text(json.dumps(self.record))
        counts,rows=audit.local_census(self.root)
        self.assertEqual(counts["local_invalid_or_untrusted"],1)
        self.assertEqual(rows,[])

    def test_source_symlink_is_rejected(self):
        source=self.root/"elsewhere"
        self.arch.rename(source)
        self.arch.symlink_to(source)
        counts,rows=audit.local_census(self.root)
        self.assertEqual(counts["local_invalid_or_untrusted"],1)
        self.assertEqual(rows,[])

    def test_remote_census_is_read_only_and_aggregate(self):
        _,rows=audit.local_census(self.root)
        fake=subprocess.CompletedProcess(["ssh"],0,'{"checked":1,"ready_absent":1}\n',"")
        with patch.object(audit.subprocess,"run",return_value=fake) as proc:
            output=audit.remote_check(rows)
        self.assertEqual(output,{"checked":1,"ready_absent":1})
        args=proc.call_args
        self.assertEqual(args.args[0][0],"ssh")
        self.assertIn('"n":',args.kwargs["input"])
        self.assertNotIn("rsync",str(args.args[0]))

    def test_remote_failure_does_not_claim_completion(self):
        _,rows=audit.local_census(self.root)
        fake=subprocess.CompletedProcess(["ssh"],255,"","authentication failed")
        with patch.object(audit.subprocess,"run",return_value=fake):
            with self.assertRaises(RuntimeError):
                audit.remote_check(rows)


if __name__=="__main__":
    unittest.main(verbosity=2)

