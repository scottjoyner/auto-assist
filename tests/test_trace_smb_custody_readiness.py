"""Security-only assessment: positive fixtures never authorize production."""

from __future__ import annotations

import copy

import pytest

from assistx.trace_smb_custody_readiness import evaluate

ARGS = {
    "expected_server_hostname": "beelink-ryzen-7-mini-pc",
    "expected_server_uuid": "0880694b-51c8-42be-b8d2-c8ac119f2b58",
    "expected_server_mount": "/nas",
    "expected_share": "assistx-trace-custody",
    "expected_server_path": "/nas/assistx-trace-custody",
    "expected_client_source": "//192.168.1.202/assistx-trace-custody",
    "expected_client_target": "/mnt/assistx-trace-custody",
    "dedicated_user": "assistxtrace",
}


@pytest.fixture
def secure_fixture():
    return {
        "server": {
            "hostname": "beelink-ryzen-7-mini-pc",
            "capture": "testparm-findmnt-stat-getfacl",
            "mount_fstype": "btrfs",
            "mount_uuid": "0880694b-51c8-42be-b8d2-c8ac119f2b58",
            "mount_target": "/nas",
            "share": {
                "name": "assistx-trace-custody",
                "path": "/nas/assistx-trace-custody",
                "create mask": "0600",
                "directory mask": "0700",
                "valid users": "assistxtrace",
                "force user": "assistxtrace",
                "guest ok": "No",
                "read only": "No",
                "wide links": "No",
                "follow symlinks": "No",
                "force create mode": "",
                "force directory mode": "",
            },
            "directory": {
                "mode": "0700",
                "owner": "assistxtrace",
                "acl_user": "rwx",
                "acl_group": "---",
                "acl_other": "---",
                "acl_extra": "none",
            },
        },
        "client": {
            "fstype": "cifs",
            "source": "//192.168.1.202/assistx-trace-custody",
            "target": "/mnt/assistx-trace-custody",
            "file_mode": "0600",
            "dir_mode": "0700",
            "destination_mode": "0700",
            "mounted": "yes",
        },
    }


def test_dedicated_synthetic_share_eligible_only_for_pilot(secure_fixture):
    report = evaluate(secure_fixture, **ARGS)
    assert report["eligible_for_bounded_write_pilot"]
    assert report["production_custody_approved"] is False
    assert len(report["unresolved_even_if_eligible"]) == 3


@pytest.mark.parametrize(
    ("section", "field", "changed", "code"),
    [
        ("server", "mount_uuid", "A41E-D7FB", "SERVER_VOLUME_UUID_MISMATCH"),
        ("server", "capture", "", "SERVER_EVIDENCE_INCOMPLETE"),
        ("share", "create mask", "0644", "SMB_CREATE_MASK_PERMISSIVE"),
        ("share", "directory mask", "0755", "SMB_DIRECTORY_MASK_PERMISSIVE"),
        ("share", "valid users", "assistxtrace deathstar", "SMB_USER_ACCESS_NOT_ISOLATED"),
        ("share", "force user", "scott", "SMB_NONDEDICATED_UNIX_OWNER"),
        ("share", "guest ok", "yes", "SMB_GUEST_NOT_EXPLICITLY_DENIED"),
        ("share", "follow symlinks", "yes", "SMB_UNSAFE_LINK_POLICY"),
        ("share", "path", "/nas/fileserver", "SHARE_PATH_MISMATCH"),
        ("directory", "mode", "0755", "SERVER_DIRECTORY_PERMISSIONS_UNSAFE"),
        ("directory", "owner", "scott", "SERVER_DIRECTORY_OWNER_WRONG"),
        ("directory", "acl_group", "r-x", "SERVER_ACL_NOT_EXCLUSIVE"),
        ("directory", "acl_extra", "u:other:rwx", "SERVER_ACL_NOT_EXCLUSIVE"),
        ("client", "file_mode", "0755", "CLIENT_FILE_MODE_UNSAFE"),
        ("client", "dir_mode", "0755", "CLIENT_DIRECTORY_MODE_UNSAFE"),
        ("client", "source", "//192.168.1.202/fileserver", "CLIENT_SHARE_SOURCE_WRONG"),
        ("client", "mounted", "no", "CLIENT_MOUNT_NOT_ACTIVE"),
    ],
)
def test_custody_gate_fails_closed_on_single_bad_fact(secure_fixture, section, field, changed, code):
    evidence = copy.deepcopy(secure_fixture)
    obj = evidence["server"].get(section, {}) if section in {"share", "directory"} else evidence[section]
    obj[field] = changed
    result = evaluate(evidence, **ARGS)
    assert not result["eligible_for_bounded_write_pilot"]
    assert result["production_custody_approved"] is False
    assert code in result["reason_codes"]


def test_actual_observed_shared_fileserver_is_not_eligible(secure_fixture):
    """Grounded read-only observations, never a fabricated secure share."""
    evidence = copy.deepcopy(secure_fixture)
    evidence["server"]["share"].update(
        {
            "name": "fileserver",
            "path": "/nas/fileserver",
            "create mask": "0644",
            "directory mask": "0755",
            "valid users": "scott deathstar kipnerter",
            "force user": "scott",
            "follow symlinks": "yes",
        }
    )
    evidence["server"]["directory"].update(
        {
            "mode": "0755",
            "owner": "scott",
            "acl_group": "r-x",
            "acl_other": "r-x",
        }
    )
    evidence["client"].update(
        {
            "source": "//192.168.1.202/fileserver",
            "target": "/nas",
            "file_mode": "0755",
            "dir_mode": "0755",
            "destination_mode": "0755",
        }
    )
    report = evaluate(evidence, **ARGS)
    assert not report["eligible_for_bounded_write_pilot"]
    assert "SMB_USER_ACCESS_NOT_ISOLATED" in report["reason_codes"]
    assert "SMB_NONDEDICATED_UNIX_OWNER" in report["reason_codes"]
    assert "SERVER_DIRECTORY_PERMISSIONS_UNSAFE" in report["reason_codes"]
    assert report["production_custody_approved"] is False


@pytest.mark.parametrize("evidence", [{}, {"server": {}}, {"server": {}, "client": {}}])
def test_missing_observation_cannot_claim_approval(evidence):
    r = evaluate(evidence, **ARGS)
    assert not r["eligible_for_bounded_write_pilot"]
    assert r["reason_codes"]
    assert r["production_custody_approved"] is False
