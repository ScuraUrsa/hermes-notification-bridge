# syntax=docker/dockerfile:1
# Notification Bridge — multi-stage Python build

# ============================================================
# Stage 1: Build — install dependencies
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 2: Runtime — minimal image
# ============================================================
FROM python:3.11-slim

# Create non-root user
RUN groupadd -r bridge && useradd -r -g bridge -d /app -s /sbin/nologin bridge

WORKDIR /app

# Copy installed packages from builder (system-wide)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY bridge/ bridge/
COPY run_bridge.py .

# Ensure scripts are executable and data dir exists
RUN mkdir -p /data && \
    chmod -R a+rX . && \
    chown -R bridge:bridge /app /data

# Switch to non-root user
USER bridge

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8655/health')" || exit 1

EXPOSE 8655

CMD ["python", "run_bridge.py"]
