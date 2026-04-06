# ── Stage 1: build dependencies ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to build some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

# Install package + all deps into /install
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -e ".[dev]" \
 && pip install --prefix=/install --no-cache-dir .


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="TestBuddy / QA Agent"
LABEL org.opencontainers.image.description="AI-powered platform QA and testing assistant"

# Playwright system dependencies + Xvfb for headed (UI) mode in K8s
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime deps
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpangocairo-1.0-0 libpango-1.0-0 libcairo2 libx11-6 libxcb1 \
    libxext6 fonts-liberation wget ca-certificates \
    # Virtual display — lets Playwright run headed inside the pod
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./src/
COPY config/ ./config/
COPY entrypoint.sh /entrypoint.sh

# Install playwright browsers (chromium only) into a shared path
# so they are accessible when the app runs as the non-root buddy user
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install playwright \
 && playwright install chromium \
 && playwright install-deps chromium \
 && chmod -R 755 /ms-playwright

# Data directories (overridden by PVC in K8s)
RUN mkdir -p /data/runs /data/audit /data/snapshots /data/evidence

# Non-root user for security
RUN useradd -r -u 1001 -g root buddy \
 && chown -R buddy:root /app /data \
 && chmod +x /entrypoint.sh
USER 1001

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QA_AGENT_CONFIG_PATH=/app/config/default.yaml \
    RUNS_STORAGE_ROOT=/data/runs \
    BUDDY_AUDIT_PATH=/data/audit/buddy_audit.jsonl \
    BUDDY_SNAPSHOT_DIR=/data/snapshots

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD wget -qO- http://localhost:8000/health || exit 1

CMD ["/entrypoint.sh"]
