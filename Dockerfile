# Dockerfile — AssistX + Hermes container runtime
FROM python:3.12-slim AS base

# ---- OS deps: runtime tooling for Hermes/OpenCode/MCP ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    jq \
    openssh-client \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---- Node.js / npm (for MCP servers) ----
RUN curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource-setup.sh \
    && bash /tmp/nodesource-setup.sh \
    && rm /tmp/nodesource-setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version && npm --version

# ---- Workdir & env ----
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    XDG_CACHE_HOME=/app/.cache \
    TRANSCRIPTIONS_ROOT=/app/transcriptions

# ---- AssistX dependencies stay in the application interpreter ----
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---- Hermes owns a separate interpreter: its pins conflict with AssistX. ----
COPY requirements-hermes.txt /app/requirements-hermes.txt
RUN python -m venv /opt/hermes
RUN /opt/hermes/bin/python -m pip install --no-cache-dir -r /app/requirements-hermes.txt
RUN /opt/hermes/bin/python -m pip check
RUN ln -s /opt/hermes/bin/hermes /usr/local/bin/hermes
# Check both dependency sets; never add the Hermes venv to application PATH.
RUN pip check
RUN which hermes && hermes --version

# ---- Install OpenCode CLI ----
# OpenCode is a ~180MB precompiled ELF binary. It is mounted from the host at
# runtime (docker-compose volume), not baked into the image to keep layers
# small.  See the hermes-adapter service volume in docker-compose.yml.

# ---- App code & assets ----
COPY src /app/src
COPY templates /app/templates
COPY static /app/static

# ---- Container Hermes defaults (seeded into volume on first start) ----
COPY hermes_config.yaml /app/hermes-home.defaults/config.yaml
RUN mkdir -p /app/hermes-home.defaults/sessions /app/hermes-home.defaults/logs \
    /app/hermes-home.defaults/cache /app/hermes-home.defaults/skills \
    /app/hermes-home.defaults/memories /app/hermes-home.defaults/profiles \
    /app/hermes-home.defaults/tmp

# ---- Entrypoint that initialises HERMES_HOME on first start ----
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Ensure all runtime dirs exist and are writable
RUN mkdir -p /app/.cache /app/transcriptions /app/artifacts

EXPOSE 8000 8100
CMD ["uvicorn", "assistx.api_router:app", "--host", "0.0.0.0", "--port", "8000"]
