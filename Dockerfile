# Dockerfile
FROM python:3.12-slim

# ---- OS deps (ffmpeg for webm→wav etc.) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

# ---- Workdir & env ----
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    XDG_CACHE_HOME=/app/.cache \
    TRANSCRIPTIONS_ROOT=/app/transcriptions

# ---- Python deps ----
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---- App code & assets ----
COPY src /app/src
COPY templates /app/templates
COPY static /app/static

# Ensure cache/transcriptions exist and are writable
RUN mkdir -p /app/.cache /app/transcriptions /app/artifacts

EXPOSE 8000
CMD ["uvicorn", "assistx.api:app", "--host", "0.0.0.0", "--port", "8000"]
