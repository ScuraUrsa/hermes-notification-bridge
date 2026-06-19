# Notification Bridge — Docker image
# Multi-stage build: slim runtime, no dev dependencies

FROM python:3.12-slim AS builder

WORKDIR /app

COPY bridge/ bridge/
COPY run_bridge.py .

RUN pip install --no-cache-dir aiohttp

FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /app /app

RUN mkdir -p /data

ENV BRIDGE_PORT=8655
ENV BRIDGE_HOST=0.0.0.0
ENV BRIDGE_AUTH_KEY=hermes-bridge-dev-key
ENV BRIDGE_DB_PATH=/data/messages.db
ENV BRIDGE_HEARTBEAT_INTERVAL=30
ENV BRIDGE_MAX_MESSAGES=1000

EXPOSE 8655

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; import json; r=urllib.request.urlopen('http://localhost:8655/health'); assert json.loads(r.read())['status']=='ok'"

CMD ["python3", "run_bridge.py"]
