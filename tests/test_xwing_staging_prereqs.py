"""Offline-only qualification checks for Xwing release-custody prerequisites."""
import importlib.util
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / "scripts" / "inspect_xwing_staging_prereqs.py"
spec = importlib.util.spec_from_file_location("physical_staging_prereq", P)
assert spec and spec.loader
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def ready():
    return {
        "hostname": "xwing",
        "remote_docker_override": False,
        "docker_context": "default",
        "docker_reachable": True,
        "image_matches_pin": True,
        "platform_matches": True,
        "disk_free_bytes": 70 * 1024 ** 3,
        "memory_available_kb": 5 * 1024 ** 2,
        "port_in_use": False,
    }


def test_pass_means_only_prerequisites_not_authorization(ready):
    result = subject.assess(ready)
    assert not result["hold_reasons"]
    assert result["verdict"] == "XWING_READ_ONLY_STAGING_PREREQUISITES_PASS_NOT_RELEASE_AUTHORITY"
    assert result["operator_release_approved"] is False
    assert result["isolated_canary_executed"] is False
    assert result["independent_signature_verified_on_host"] is False
    assert result["physical_rollback_verified"] is False


@pytest.mark.parametrize(("field", "replacement", "reason"), [
    ("hostname", "x1-370", "WRONG_HOST"),
    ("remote_docker_override", True, "REMOTE_DOCKER_CONTEXT"),
    ("docker_context", "untrusted", "NONDEFAULT_DOCKER_CONTEXT"),
    ("docker_reachable", False, "DOCKER_NOT_REACHABLE"),
    ("image_matches_pin", False, "SIGNED_IMAGE_NOT_CACHED_AS_EXPECTED"),
    ("platform_matches", False, "WRONG_IMAGE_PLATFORM"),
    ("disk_free_bytes", 4 * 1024 ** 3, "INSUFFICIENT_DOCKER_SPACE"),
    ("memory_available_kb", 2 * 1024 ** 2, "INSUFFICIENT_MEMORY"),
    ("port_in_use", True, "RESERVED_PORT_BUSY"),
])
def test_denies_any_failed_requirement(ready, field, replacement, reason):
    ready[field] = replacement
    result = subject.assess(ready)
    assert result["verdict"] == "XWING_STAGING_HOLD"
    assert reason in result["hold_reasons"]
    assert result["operator_release_approved"] is False
    assert result["production_image_or_service_changed"] is False


@pytest.mark.parametrize("bad", [
    "", "latest", "sha256:short", "sha256:" + "Z" * 64,
    "sha256:" + "a" * 63, "sha256:" + "a" * 65,
])
def test_reject_unpinned_or_non_digest_images(bad):
    with pytest.raises(subject.PreflightHold, match="INVALID_PINNED_IMAGE_ID"):
        subject.require_digest(bad)


def test_accept_immutable_digest_only():
    assert subject.require_digest(DIGEST) == DIGEST


def test_meminfo_parsing():
    assert subject.memory_available_kb("MemTotal: 9000 kB\nMemAvailable: 4821 kB\n") == 4821
    with pytest.raises(subject.PreflightHold, match="MEMORY_PROVENANCE_UNAVAILABLE"):
        subject.memory_available_kb("MemTotal: 1000 kB\n")


def test_multiple_failures_all_preserved(ready):
    ready["image_matches_pin"] = False
    ready["port_in_use"] = True
    assert subject.assess(ready)["hold_reasons"] == [
        "SIGNED_IMAGE_NOT_CACHED_AS_EXPECTED", "RESERVED_PORT_BUSY",
    ]
