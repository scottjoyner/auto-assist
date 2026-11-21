# Research-only, nondeployable rootless entrypoint overlay.
# Supply an independently reviewed immutable base reference explicitly.
ARG HERMES_BASE_IMAGE
FROM ${HERMES_BASE_IMAGE}
COPY --chmod=0755 entrypoint.sh /entrypoint.sh
LABEL assistx.release.authorized="false"
LABEL assistx.runtime.mode="synthetic-rootless-research-only"
