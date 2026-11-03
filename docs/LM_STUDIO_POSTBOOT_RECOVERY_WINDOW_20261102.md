# LM Studio post-boot API recovery-window hardening — Nov 2, 2026

**Draft only. No production timer edits, model loading or approval.**

## Live observed reason for review

x1-370 rebooted through the orderly systemd/logind shutdown path at
approximately 19:30:40 EST. The next boot was recorded at 19:31:20.

On that boot the already deployed lm-studio-api-start.service began at
19:31:37, completed at 19:32:04 and reported Server is now running.
The loopback API subsequently stopped accepting traffic by 19:34:38.
The distinct lm-studio-api-recheck.timer did not fire until the
five-minute calendar boundary at 19:35:02. Its check restarted only the
existing API starter and reported API_WATCHDOG: recovered at 19:35:31.
By 19:36:20, loopback/LAN/Tailscale model-catalog GETs all returned HTTP
200. No K2 model was resident. The cause of the first post-boot LM
Studio process exit was NOT independently identified. The inspected
kernel journal did not establish an OOM kill or kernel panic.

A future boot could similarly appear successful initially and lose
the serving process before the first five-minute calendar check.

## Opt-in unit-only proposed change

The lm-studio-api-recheck.timer gains:
- OnStartupSec=90s: initial check after user manager startup
- OnUnitInactiveSec=60s: recheck after previous oneshot completion
- Original OnCalendar=*:0/5 and Persistent=true retained
- AccuracySec tightened from 30s to 10s

The oneshot gains After=lm-studio-api-start.service in addition to
network-online.target, to order a recheck after the original starter
when both startup jobs are in flight.

The actual health check is unchanged: fixed local GET /v1/models,
redirects and proxy use disabled, max 3-second request timeout,
bounded 64 KiB JSON model catalog. If unavailable, it restarts ONLY
lm-studio-api-start.service once, then waits and reprobes. No GGUF load,
runtime admission, graph mutation or paid hosted API is performed.

The additional lightweight GET is low overhead but not zero-cost.
The timer may fire during boot pressure or near calendar boundaries.
A single systemd one-shot service cannot overlap itself, but this
does NOT prevent a model process from crashing again.

## Local software checks

- All four original watchdog unit tests pass.
- Three new timer/service tests check the schedule, security restrictions,
  unit ordering and healthy-API nonrestart behavior.
- Python compilation, lint and workflow schema checks pass.
- systemd-analyze verify was run with the proposed units; it also printed
  warnings about unrelated host units, so this is not a whole-host
  clean validation claim.

## Promotion, measurement and rollback

Do not change the live timer as part of PR review. Operator to review
the proposed cadence and test in a maintenance window first. After an
authorized update, verify real NextElapseUSecRealtime, log rate and no
overlapping restart jobs, check for unintended model loads, and compare
downtime during a separately approved reboot/recovery exercise.

Rollback: restore the original two reviewed unit files, reload the
user systemd manager, restart only the dedicated recheck timer and
confirm original calendar cadence. Preserve starter RemainAfterExit=yes.
Do not restore the older ineffective starter timer.

K2 still fails reviewed LM Studio version, physical model and process
admission and signed canonical approval. Shorter API recovery cannot
authorize production inference.
