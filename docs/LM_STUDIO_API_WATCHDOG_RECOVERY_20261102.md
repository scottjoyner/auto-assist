# x1-370 LM Studio API recheck (non-admitting host recovery)

Scope: API bootstrap only. No runtime projection renewal, model loading,
provider credentials, routing permission changes, or inference requests.

## Verified diagnosis (November 2, 2026)

The original lm-studio-api-start.service is Type=oneshot,
RemainAfterExit=yes. This keeps the launched LM Studio process in its active
systemd unit, but a timer targeting the already-active starter cannot repeat
the check. Its original timer had no next scheduled elapse. K2 resident recovery
failed with K2_RESTORE_BLOCKED: URLError while the API was unavailable.
AssistX reported its LLM dependency down; Redis and Neo4j were healthy.
The canonical runtime approval generation 896 was expired.

Changing the starter to RemainAfterExit=no was tested and reverted: the
model API did not remain available after the service exited. Keep that
starter with RemainAfterExit=yes. The dedicated recheck service is a separate
short-lived one-shot and can safely trigger the starter through systemctl.

## Deployment to x1-370 after operator review

Copy scripts/lmstudio-api-recheck.py into
/home/scott/.local/bin/lmstudio-api-recheck.py, mode 0755.

Copy deploy/systemd/user/lm-studio-api-recheck.service and
deploy/systemd/user/lm-studio-api-recheck.timer into
/home/scott/.config/systemd/user/, both mode 0644.

Reload systemd user units; start the recheck service for a one-time probe,
then enable/start the dedicated five-minute timer. Only after confirming the
new timer has an actual next elapse, disable the old ineffective
lm-studio-api-start.timer. Keep the existing API starter unchanged.

The probe makes a bounded, no-redirect GET to localhost /v1/models without
inherited proxy. A valid empty catalog means API readiness, not model
residency or inference readiness. If unavailable it restarts only the exact
existing API starter once and reprobes. It does not call any model.

## Verify and rollback

Verify the original starter still reports RemainAfterExit=yes.
Verify the new timer reports ActiveState=active,
Unit=lm-studio-api-recheck.service and a nonempty NextElapseUSecRealtime.
Check its scheduled firing in the user journal. Verify the local model
catalog responds with HTTP 200. Do not infer token utilization or admission.

Rollback: disable/stop lm-studio-api-recheck.timer, remove only the new
recheck timer/service and installed script, reload user systemd.
Do not reenable the superseded original timer without fixing its activation.
Do not change runtime approval or production router admission.
