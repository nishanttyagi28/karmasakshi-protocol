FROM python:3.14-slim AS base

WORKDIR /app

# Install build dependencies only long enough to build the wheel, then discard them.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[api]"

# Non-root user and durable application-data mount point for the runtime container.
RUN useradd --create-home --uid 1000 karmasakshi && \
    mkdir -p /data && \
    chown karmasakshi:karmasakshi /data
ENV KARMASAKSHI_DATA_DIR=/data
USER karmasakshi

EXPOSE 8000

# Container-level liveness probe against the unauthenticated /health route
# (safe to expose: it reveals no state, just process liveness). Most PaaS
# hosts (Render, Fly, Railway, ECS, k8s) also run their own HTTP health
# check against /health independently of this.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=2)" || exit 1

# Runs the FastAPI control plane by default. Listens on $PORT if the host
# platform sets it (Render/Fly/Railway all do), falling back to 8000 for
# local `docker run`. Override the command to run the CLI instead, e.g.:
#   docker run --rm karmasakshi-protocol karmasakshi demo --all
CMD ["sh", "-c", "python -m uvicorn karmasakshi.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
