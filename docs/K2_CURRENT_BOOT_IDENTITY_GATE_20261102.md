# K2 witness receipt must bind to independently trusted current boot — Nov 2, 2026

RESEARCH ONLY / NO PRODUCTION TRUST PINS / NO PRODUCTION ADMISSION.

## Observed failure mode

x1-370 completed an orderly reboot near Nov 2, 2026 19:30:40 EST,
restarted at about 19:31:20, and its first LM Studio local service
reported successful startup at 19:32:04. Local API access was lost by
19:34:38. Its bounded watchdog recovered availability by 19:35:31,
but K2 remained unloaded.

Before this change, the offline signed-witness verifier checked that
the included kernel boot-ID SHA-256 was syntactically a 64-character
hex string. It did NOT require that digest to match a separately trusted
measurement of the CURRENT serving node boot. An authentic receipt
signed shortly before a reboot could remain within its allowed
120-second freshness budget after a reboot, potentially misleading
an operator about whether PID/socket observations apply to the new boot.

## Conservative additive verification gate

The research verifier adds an intentionally unconfigured policy root:

    TRUSTED_CURRENT_BOOT_ID_SHA256 = ""

The verifier checks this root before accepting any signed receipt,
and refuses with independent_current_boot_identity_missing when
unconfigured. When a separately trusted measurement of the CURRENT
x1 kernel boot-ID SHA-256 exists, the receipt's process.boot_id_sha256
must match it exactly or the verifier refuses with
witness_observation_from_different_boot.

The source of truth must NOT be populated from the receipt itself,
from a caller-selected CLI flag, from the same untrusted model API,
or from an off-host agent with shared unrestricted administration
without independently reviewed and approved custody.

No CLI flag is provided to replace the boot-ID trust pin. Isolated
tests may inject short-lived synthetic reference boot digests.

## Test scope and remaining limitations

Five adversarial cases exercise absent trust root, authentic
old-boot receipt after trusted boot changes, altered boot ID
inside a signed observation, invalid boot digest format and the
production pin being deliberately empty. Combined tests: 61 PASS.

The new pin is not itself hardware attestation, proof of socket
ownership or a sufficient admission decision. Even matching signed
receipt output remains decision=HOLD, can_dispatch=false,
independent_host_custody_verified=false. A process can change
within the same boot; a final source-attested execution-time
identity verification and an independently approved operator
canonical CAS would still be required.

No host reboot, kernel changes, inference, model load, real trace,
key provisioning, Neo4j writes or production timer changes occur.
