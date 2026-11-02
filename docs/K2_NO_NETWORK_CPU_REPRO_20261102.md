# K2 GGUF network-disabled CPU reproducibility — November 2, 2026

**Research result: CROSS_RUNTIME_SYNTHETIC_PASS. Production admission: NO-GO.**

This experiment checks the same SHA-256-pinned K2 GGUF independently of
the shared LM Studio application. It does not establish LM Studio
compatibility at the target 64k context/four-slot concurrency, or
provide signed authority for production routing.

## Isolation and resource contract

A disposable Docker container uses the existing local Python 3.12 image,
existing prebuilt CPU-only llama.cpp server, and pinned on-disk K2 GGUF.
All mounts are read-only (model, server binaries, and host runtime libs).
The host dynamic loader/libraries are required because this compiled
binary needs a newer glibc than the existing image provides.

- Network mode NONE and empty published ports (verified with Docker inspect).
- Read-only root, not privileged, all capabilities dropped,
  no-new-privileges, UID/GID 1000:1000, PID limit 64.
- CPU cap 3 cores, memory/swap hard cap 6 GiB total, tmpfs 96 MiB.
- Local existing image only; never pull or download.
- GGUF SHA-256 pinned to
  371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8.
- Serving address 127.0.0.1:18721 INSIDE the container namespace.
- Synthetic requests only via Python client inside the container.
- Context 4096, 2 server slots, 0 GPU layers, 3 CPU threads.
- No use of the existing LM Studio shared server, LAN/Tailscale proxy,
  Auto-Router, AssistX, live traces, real prompts, or hosted APIs.
- Image, model integrity, memory and disk guards run before creating the
  container. Runtime isolation settings must match before any requests.
- Container cleanup is mandatory for PASS, even after error.
- Evidence is an immutable, new, mode-0600 JSON file.

## Actual bounded measurements

UTC start: 2026-11-02 22:37:38.

Local evidence file:
 /home/scott/.local/state/assistx-admission-preflight/k2-no-network-cpu-20261102T223738Z.json

Evidence SHA-256:
 2ccca8c78f27b51af95be07fa9165ec85130d28f3441a67aa292cc3b7bc4d055

| Case | Mode | HTTP | Prompt | Generated | Visible | Finish | Seconds |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: |
| 0 | Sequential | 200 | 16 | 48 | No | length | 1.652 |
| 1 | Sequential | 200 | 24 | 40 | Yes | stop | 1.403 |
| 2 | Concurrent | 200 | 23 | 48 | No | length | 1.862 |
| 3 | Concurrent | 200 | 16 | 48 | No | length | 1.861 |

All four requests returned valid provider-reported usage:
**79 prompt + 184 generated = 263 total tokens**.
One completion produced visible answer text and stop. Three reached
the 48-token output bound while reasoning. Small-request wall times
are not a steady-state performance benchmark.

Docker inspect verified network mode none, zero host port bindings,
read-only root, unprivileged mode and dropped capabilities. The server
became healthy and the container was removed. No leftover test
container was observed.

These measurements are synthetic CPU experiment counts and must
not be added to production fleet 24-hour usage totals.

## Still blocking production

The reviewed LM Studio version and current installed version differ.
An isolated llama.cpp demonstration does not qualify LM Studio 64k/four
slots, or certify physical LAN/Tailscale admission paths. Production
AssistX signed runtime evidence remains expired. A separately reviewed,
physical-model/process/path/capacity attestation and operator-approved
canonical generation advance is required before Auto-Router dispatch.
Do not auto-extend expired leases, alter approval pins, or bypass the
production signed projection.
