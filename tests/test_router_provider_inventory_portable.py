"""Pure-stdlib safety test suite, runs unchanged on Lenovo without pip."""

from __future__ import annotations

import unittest
from urllib.error import HTTPError

from assistx.router_provider_inventory import (
    fetch_provider_inventory,
    parse_provider_inventory,
)

EFFECTIVE = {
    "schema_version": 1,
    "kind": "EFFECTIVE_ROUTER_MEMORY_READ_ONLY",
    "state_source": "LIVE_ASGI_STATE_NOT_BOOTSTRAP_PROVIDER_FILE",
    "no_probes_or_dispatch": True,
    "may_prove_model_loaded": False,
    "may_modify_routes": False,
    "provider_count": 2,
    "projection": {"state": "NO_FRESH_SIGNED_PROJECTION_PROOF"},
    "providers": [
        {
            "provider_id": "destroyer-ornith",
            "node_id": "DESTROYER",
            "enabled_in_router_memory": True,
            "effective_base_url": {"status": "PARSED_NOT_PROBED", "hostname": "10.0.0.5"},
            "effective_access_urls": [
                {"status": "PARSED_NOT_PROBED", "hostname": "100.64.0.5"},
                {"status": "INVALID", "hostname": "user:pass@bad.example"},
            ],
            "configured_models": [
                {"provider_model": "Ornith-1.5", "alias": "ornith"},
                {"provider_model": "Ornith-1.5", "alias": "ornith"},
            ],
            "resident_model_identity_verified": False,
            "service_reachability_verified": False,
            "admission_granted": False,
        },
        {
            "node_id": "x1-370",
            "enabled_in_router_memory": False,
            "configured_models": [{"provider_model": "blocked-model"}],
            "effective_base_url": {"status": "PARSED_NOT_PROBED", "hostname": "10.0.0.6"},
        },
    ],
}
LEGACY = {
    "providers": [
        {
            "enabled": True,
            "node_id": "DESTROYER",
            "runtime_instance_id": "destroyer-1236",
            "base_url": "http://user:secret@100.64.0.5:1236/v1?token=hidden",
            "models": [{"provider_model": "Ornith-1.5"}],
        },
        {
            "enabled": False,
            "node_id": "x1-370",
            "base_url": "http://10.0.0.6:1234",
            "models": [{"provider_model": "disabled-model"}],
        },
    ]
}


class SafeProviderCatalog(unittest.TestCase):
    def test_sanitized_effective_snapshot_identifies_both_networks(self):
        result = parse_provider_inventory(EFFECTIVE, effective=True)
        for key in ("destroyer", "destroyer-ornith", "10.0.0.5", "100.64.0.5"):
            self.assertEqual(result[key], ["Ornith-1.5"])
        self.assertNotIn("x1-370", result)
        self.assertNotIn("user:pass@bad.example", result)

    def test_unfresh_projection_is_only_configured_inventory(self):
        self.assertEqual(parse_provider_inventory(EFFECTIVE, effective=True)["destroyer"], ["Ornith-1.5"])

    def test_no_token_never_calls_network(self):
        calls = []

        def fetch(*args, **kwargs):
            calls.append((args, kwargs))
            return EFFECTIVE, 1.0

        self.assertEqual(
            fetch_provider_inventory(base_url="http://router:8088", override_url="", admin_token="", fetch_json=fetch),
            {},
        )
        self.assertEqual(calls, [])

    def test_default_prefers_sanitized_effective(self):
        seen = []

        def fetch(url, path, **kwargs):
            seen.append((url, path, kwargs))
            return EFFECTIVE, 1.0

        result = fetch_provider_inventory(
            base_url="http://router:8088/", override_url="", admin_token="synthetic", fetch_json=fetch
        )
        self.assertEqual(result["destroyer"], ["Ornith-1.5"])
        self.assertEqual(seen, [("http://router:8088/admin/providers/effective", "", {"admin_token": "synthetic"})])

    def test_missing_effective_404_falls_back_with_same_token(self):
        seen = []

        def fetch(url, path, **kwargs):
            seen.append((url, kwargs["admin_token"]))
            if url.endswith("/effective"):
                raise HTTPError(url, 404, "missing", {}, None)
            return LEGACY, 1.0

        result = fetch_provider_inventory(
            base_url="http://router:8088", override_url="", admin_token="synthetic", fetch_json=fetch
        )
        self.assertEqual(result["destroyer"], ["Ornith-1.5"])
        self.assertEqual(result["destroyer-1236"], ["Ornith-1.5"])
        self.assertEqual(result["100.64.0.5"], ["Ornith-1.5"])
        self.assertEqual(len(seen), 2)
        self.assertEqual({token for _, token in seen}, {"synthetic"})

    def test_legacy_override_preserved_but_requires_token(self):
        seen = []

        def fetch(url, path, **kwargs):
            seen.append(url)
            return LEGACY, 1.0

        result = fetch_provider_inventory(
            base_url="http://router:8088",
            override_url="http://router:8088/admin/providers",
            admin_token="synthetic",
            fetch_json=fetch,
        )
        self.assertEqual(result["destroyer"], ["Ornith-1.5"])
        self.assertEqual(seen, ["http://router:8088/admin/providers"])

    def test_cross_origin_overrides_never_receive_admin_token(self):
        seen = []

        def fetch(*args, **kwargs):
            seen.append((args, kwargs))
            return LEGACY, 1.0

        for override in (
            "http://other-router:8088/admin/providers",
            "https://router:8088/admin/providers",
            "http://router:9090/admin/providers",
            "http://router:8088@attacker.test/admin/providers",
            "http://user:password@router:8088/admin/providers",
            "http://router:bad/admin/providers",
            "http://router:8088/admin/ops/summary",
            "http://router:8088/admin/providers?debug=1",
            "http://router:8088/admin/providers#fragment",
            "http://router:8088/admin/providers/effective/extra",
            "http://router:8088/api/fleet/nodes",
        ):
            with self.subTest(override=override):
                self.assertEqual(
                    fetch_provider_inventory(
                        base_url="http://router:8088",
                        override_url=override,
                        admin_token="synthetic",
                        fetch_json=fetch,
                    ),
                    {},
                )
        self.assertEqual(seen, [])

    def test_wrong_token_does_not_downgrade(self):
        for code in (400, 401, 403, 408, 429, 500, 503):
            with self.subTest(code=code):
                seen = []

                def fetch(url, path, **kwargs):
                    seen.append(url)
                    raise HTTPError(url, code, "denied", {}, None)

                result = fetch_provider_inventory(
                    base_url="http://router:8088", override_url="", admin_token="wrong", fetch_json=fetch
                )
                self.assertEqual(result, {})
                self.assertEqual(len(seen), 1)

    def test_endpoint_body_mismatch_does_not_downgrade(self):
        seen = []

        def fetch(url, path, **kwargs):
            seen.append(url)
            return LEGACY, 1.0

        self.assertEqual(
            fetch_provider_inventory(
                base_url="http://router:8088", override_url="", admin_token="synthetic", fetch_json=fetch
            ),
            {},
        )
        self.assertEqual(len(seen), 1)

    def test_malformed_snapshot_fail_closed(self):
        cases = [
            {"schema_version": 2},
            {"kind": "LEGACY_RAW_PROVIDERS"},
            {"no_probes_or_dispatch": False},
            {"may_prove_model_loaded": True},
            {"may_modify_routes": True},
            {"provider_count": 1},
            {"providers": [None, None]},
            {"providers": "not-list"},
        ]
        for change in cases:
            with self.subTest(change=change):
                sample = EFFECTIVE | change
                self.assertEqual(parse_provider_inventory(sample, effective=True), {})

    def test_disabled_legacy_and_malformed_rows_fail_closed(self):
        self.assertEqual(parse_provider_inventory(LEGACY, effective=False)["destroyer"], ["Ornith-1.5"])
        self.assertNotIn("x1-370", parse_provider_inventory(LEGACY, effective=False))
        self.assertEqual(parse_provider_inventory({"providers": [1]}, effective=False), {})
        self.assertEqual(parse_provider_inventory({"providers": [{}] * 1001}, effective=False), {})


if __name__ == "__main__":
    unittest.main()
