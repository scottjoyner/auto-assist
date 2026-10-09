# Trace custody SMB isolation: read-only server + client gate

**Observation date:** 2026-10-09, America/New_York (read-only fleet inspection).

**Status:** DENIED for current SMB share. Proposed config is **NOT APPLIED**.

**Issue:** [AssistX NAS custody #127](https://github.com/scottjoyner/auto-assist/issues/127);
stacked on [draft PR #135](https://github.com/scottjoyner/auto-assist/pull/135)
and ultimately [draft PR #119](https://github.com/scottjoyner/auto-assist/pull/119).

## Observed, independently collected facts

Read-only SSH to `beelink` and local CIFS inspection established:

| Field | Observed |
| --- | --- |
| Server | `BEELINK-RYZEN-7-MINI-PC` |
| Filesystem behind existing export | Btrfs `/dev/sdd2`, mounted at `/nas` |
| Btrfs UUID | `0880694b-51c8-42be-b8d2-c8ac119f2b58` |
| Space | about 81 TB total, 40 TB used and 41 TB available at inspection |
| Existing Samba share | `[fileserver]`, server path `/nas/fileserver` |
| Samba masks | create `0644`, directory `0755` |
| Principal mapping | `force user = scott`; `valid users = scott deathstar kipnerter` |
| Server path actual stat/ACL | `0755 scott:scott`, owner rwx, group r-x, other r-x |
| Desktop client's `/nas` | automount + `//192.168.1.202/fileserver` CIFS |
| Client mode options | `file_mode=0755`, `dir_mode=0755`, `nounix,noperm` |

This Btrfs filesystem is a **different custody identity** from the
historical NAS5 exFAT recovery source. Do not use its spare capacity
to infer NAS5 recovery completion, authorize deletion or perform bulk
transfer.

**Result:** The deny-only evaluator produced **16 distinct reasons** why
the current share cannot qualify even for a bounded encrypted write pilot:
wrong dedicated share/path; shared SMB identities and forced Unix owner;
permissive Samba create/dir masks, directory ownership and server ACL;
missing explicit restrictive guest/link defaults; and mismatched/permissive
client source, target and modes. These reasons are based on actual
`testparm`, `findmnt`, `stat` and `getfacl` output, not just client-mode
presentation.

## Proposed separate Samba namespace (configuration preview only)

An administrator with authenticated server-side sudo/root authority must
provision a **separate, dedicated Unix/Samba service identity**, rather than
reuse any of the three users currently allowed on `fileserver`.

Example candidate for security review (NOT executed or installed):

```ini
[assistx-trace-custody]
    path = /nas/assistx-trace-custody
    browseable = no
    guest ok = no
    read only = no
    valid users = assistxtrace
    force user = assistxtrace
    force group = assistxtrace
    create mask = 0600
    directory mask = 0700
    force create mode = 0600
    force directory mode = 0700
    follow symlinks = no
    wide links = no
```

The server-side export directory must belong to the dedicated service
identity and be mode `0700` with no named-user/group ACL grants.
Protect server-side key/credential custody separately from task runners.
Never copy the existing `fileserver` multi-principal `force user`
configuration into the custody share.

A **separately mounted** client path such as
`/mnt/assistx-trace-custody` must use the new SMB share source,
with client-side modes `file_mode=0600,dir_mode=0700`. Client-side mode
flags alone prove nothing about actual server-side authorization; the
bounded real pilot must confirm a nonauthorized Samba principal cannot
read or overwrite objects. Do not remount or alter existing production
`/nas`, and do not modify NAS5 recovery.

## Reproduce observations without writes

From the isolated AssistX checkout (all collection is **read-only**):

```bash
cd /home/scott/git/wt-assistx-smb-custody-20261009

# Runs only testparm/findmnt/stat/getfacl on the server; no credentials output.
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes beelink \
    'python3 - fileserver /nas/fileserver' \
    < scripts/trace_smb_server_snapshot.py > /tmp/trace-smb-server.json
python3 scripts/trace_smb_client_snapshot.py \
    /nas/desktop-commander-traces > /tmp/trace-smb-client.json

PYTHONPATH=src python3 scripts/trace_smb_custody_report.py \
    --server-evidence /tmp/trace-smb-server.json \
    --client-evidence /tmp/trace-smb-client.json \
    --expected-server-hostname beelink-ryzen-7-mini-pc \
    --expected-server-uuid 0880694b-51c8-42be-b8d2-c8ac119f2b58 \
    --expected-server-mount /nas \
    --expected-share assistx-trace-custody \
    --expected-server-path /nas/assistx-trace-custody \
    --expected-client-source //192.168.1.202/assistx-trace-custody \
    --expected-client-target /mnt/assistx-trace-custody \
    --dedicated-user assistxtrace
```

The command exits **2** when policy fails (expected on current system).
Evidence can be regenerated after a dedicated share is provisioned.
Never replace explicit expectations with values read directly from
untrusted mount output, or the policy could trivially approve the wrong
source. A positive response is **only eligibility for a bounded write
pilot**, not production custody authorization.

## Follow-up gates and ownership

1. **Samba administrator:** review desired separate share, isolate SMB
   authentication, set server-side Unix owner and ACL, validate `testparm`
   before applying changes. Root privilege is not available to the current
   unattended Beelink SSH session (`sudo -n` requires a password).
2. **Read-only gate:** collect fresh server and client observations under
   independent identities. Reject if any of the policy reasons remain.
3. **Bounded write pilot:** only after server ACL eligibility, use a disposable
   <=10 MiB encrypted object; test exclusive no-overwrite publish, hardlink,
   fsync, and negative access with the nonauthorized principal. Then
   independently decrypt/restore and verify the signed local intent and
   ACK. Delete **no existing archive objects**.
4. **Separate witness custodian:** externally anchor append-only receipt
   state on another authority's storage/key. An HMAC ledger and ciphertext
   controlled by one host remain vulnerable to complete rollback.
5. **Production rollout remains blocked:** the existing 7 MiB journal
   admission stop stays; real node tokens/issuer routes and broad CI
   acceptance remain independent prerequisites.

The module `src/assistx/trace_smb_custody_readiness.py` is **deny-only**:
even a synthetic clean policy never returns
`production_custody_approved=true`.
