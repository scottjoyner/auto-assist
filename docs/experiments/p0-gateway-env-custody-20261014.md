# Draft gateway preflight restoration and environment snapshot custody

October 14, 2026. Scope: **source-control research only**; no execution,
production mutation, credential rotation, Apple/mobile release or failover.

## Source-pinned restoration

Two missing files were restored byte-for-byte from the historically reviewed
commit `925f3593`:
- `scripts/deploy-kipnerter-tailnet-gateway.sh`
- `.env.kipnerter-gateway.example`

The bash helper passed `bash -n` and the existing static gateway test.
It retains the exact `KIPNERTER_GATEWAY_SOURCE_SHA` gate, clean-worktree
requirement, separate legacy Caddy/Tailscale-header fence confirmation, and
the guarded 8443 Serve path. It was **not invoked**.

The env template is deny-default: loopback bind, Tailscale login header,
model override off, and empty allowlist. No secret values were read or
copied into this branch.

## Credential custody: remove from current Git tree only

The following accidentally tracked files are removed from the draft
branch's Git index, as already done in historical reviewed security commits
`1d4b433e` and `2945cee5`:
- `.env.bak-sync-20261006T051130Z`
- `.env.reconciliation.example`

Original files were **not deleted** from the existing x1-370 research
worktree. No file contents were read/printed. Their past Git history still
retains blobs; *untracking is not revocation, sanitization, or rotation*.
`.gitignore` additionally treats the retired reconciliation snapshot as
non-addable after template exemptions.

## Verification

17/17 local source-control and gateway tests passed; bash syntax and
static authorization fences passed. No deployment/run of any gateway script,
Docker or Tailscale change occurred. Hosted exact-head CI and independent
review are required.

**Hard HOLD:** Security issue #156 credential rotation/custody must still
be adjudicated against existing history, and legacy same-DB Neo4j admin
consumers still require exclusive broker migration. No topology, schema,
token/key, node, model, approval renewal, or production failover actions are
authorized by restoring a deny-by-default source helper.
