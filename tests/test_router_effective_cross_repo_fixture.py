"""Real PR41 snapshot-function fixture consumed by PR288 parser; offline."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from assistx.router_provider_inventory import parse_provider_inventory

FIXTURE = Path(__file__).resolve().parent / "fixtures/router_effective_snapshot_v1.json"


class ProducerConsumerContract(unittest.TestCase):
    def test_fixture_hash_matches_producer_projection_algorithm(self):
        fixture = json.loads(FIXTURE.read_text())
        checksum_payload = {
            "providers": fixture["providers"],
            "projection": fixture["projection"],
        }
        expected = hashlib.sha256(
            json.dumps(checksum_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        self.assertEqual(fixture["snapshot_sha256"], expected)
        self.assertEqual(fixture["kind"], "EFFECTIVE_ROUTER_MEMORY_READ_ONLY")
        self.assertEqual(fixture["state_source"], "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE")
        self.assertEqual(fixture["schema_version"], 1)

    def test_real_producer_shape_is_consumed_without_claiming_residency(self):
        fixture = json.loads(FIXTURE.read_text())
        catalog = parse_provider_inventory(fixture, effective=True)
        expected = ["ornith-1.5-synthetic"]
        for identity in ("destroyer", "destroyer.test", "100.64.0.5", "synthetic-destroyer-ornith"):
            with self.subTest(identity=identity):
                self.assertEqual(catalog[identity], expected)
        model_row = fixture["providers"][0]
        for field in ("resident_model_identity_verified", "service_reachability_verified", "admission_granted"):
            self.assertIs(model_row[field], False)
        self.assertIs(fixture["may_modify_routes"], False)
        self.assertIs(fixture["may_prove_model_loaded"], False)
        self.assertIs(fixture["no_probes_or_dispatch"], True)

    def test_public_fixture_omits_original_url_credentials_and_headers(self):
        raw = FIXTURE.read_text()
        for forbidden in (
            "synthetic-secret",
            "synthetic-user",
            "Bearer ",
            "DUMMY_ENV_DO_NOT_COPY",
            "?secret=",
            "Authorization",
            "api_key_env",
        ):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, raw)


if __name__ == "__main__":
    unittest.main()
