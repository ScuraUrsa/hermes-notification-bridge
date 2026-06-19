# Hermes Notification Bridge

Central relay for bidirectional messaging between Hermes Agent and the user.
Hermes pushes messages here; clients (Conduit, Open WebUI) subscribe via
WebSocket or SSE to receive them in real-time.

**Status:** Live in Docker on port 8655. 63 tests pass. GATE TEST verified.

## Architecture

```
Hermes Agent (any profile)
       │
       │ push_message() tool
       ▼
┌─────────────────────┐     WebSocket/SSE      ┌──────────────┐
│ Notification Bridge │ ◀────────────────────▶ │ Conduit/OWUI │
│ (port 8655)         │                        │ (phone/desk) │
│ aiohttp + SQLite    │                        └──────────────┘
└─────────────────────┘
```

## Quick Start

```bash
# Build
docker build -t hermes-notification-bridge:latest -f docker/Dockerfile .

# Run
docker run -d --name hermes-notification-bridge \
  -p 8655:8655 \
  -v bridge-data:/data \
  -e BRIDGE_AUTH_KEY=<...003e \
  -e BRIDGE_DB_PATH=/data/messages.db \
  --restart unless-stopped \
  hermes-notification-bridge:latest

# Verify
curl http://localhost:8655/health
# → {"status":"ok","service":"hermes-notification-bridge","clients":0,"uptime":2}
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/events` | SSE stream (browser/OWUI) |
| GET | `/events/ws` | WebSocket (Conduit) |
| POST | `/push` | Hermes pushes a message here |
| GET | `/messages` | Get recent messages |
| GET | `/messages/{id}` | Get single message |
| POST | `/messages/{id}/read` | Mark as read |
| GET | `/stats` | Unread count, connected clients |
| GET | `/test/last-delivery` | Test: last message |
| POST | `/test/reset` | Test: clear all messages |

## Push Payload

```json
{
  "title": "Backup Failed",
  "body": "Daily backup failed. 3 retries exhausted.",
  "priority": "high",
  "source": "cron",
  "action": {"type": "open_url", "url": "http://..."},
  "voice": false,
  "tts_text": null,
  "metadata": {"task_id": "t_abc123", "requires_reply": false}
}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_PORT` | 8655 | Listen port |
| `BRIDGE_HOST` | 0.0.0.0 | Bind address |
| `BRIDGE_AUTH_KEY` | (required) | Shared secret for Hermes → Bridge auth |
| `BRIDGE_DB_PATH` | ~/.hermes/notification-bridge/messages.db | SQLite path |
| `BRIDGE_HEARTBEAT_INTERVAL` | 30 | WebSocket/SSE heartbeat seconds |
| `BRIDGE_MAX_MESSAGES` | 1000 | Max stored messages before rotation |

## Hermes Integration

The `push_message` tool is registered under the `messaging` toolset.
Any Hermes profile with `messaging` enabled can call:

```python
push_message(
    title="Task Completed",
    body="Kanban task t_abc123 completed successfully.",
    priority="normal",
    source="kanban",
)
```

The tool is at `hermes-tool/push_message_tool.py` and auto-discovered
by Hermes's tool registry when placed in `tools/`.

## Voice Integration (Phase 4)

`voice/wyoming_bridge_poller.py` polls the Bridge for voice-flagged
messages and outputs TTS-ready text. Intended to run alongside the
Wyoming voice satellite.

```bash
python3 voice/wyoming_bridge_poller.py --bridge-url http://localhost:8655 --interval 5
```

## Testing

```bash
# Unit tests (58 tests)
python3 -m pytest tests/unit/ -v --asyncio-mode=auto

# E2E tests (5 scenarios, GATE TEST)
python3 -m pytest tests/e2e/ -v --asyncio-mode=auto

# All tests (63 tests)
python3 -m pytest tests/ -v --asyncio-mode=auto
```

## Cron Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `bridge-health-monitor` | every 15m | Health check + auto-restart |
| `bridge-demo-autonomous-push` | every 30m (3x) | Demo: Hermes pushes autonomously |
| `daily-vm-backup` | 03:00 daily | Backup + push notification |
| `notion-kanban-digest` | 07:00 daily | Digest + push notification |
| `daily-research-brief` | 08:00 daily | Brief + push notification |

## Related Repos

- **Conduit plugin:** `ScuraUrsa/hermes-conduit-bridge` — Dart package for Conduit mobile app
- **Goal doc:** `~/.hermes/goals/bidirectional-messaging.md`
- **Notion KB:** "Notification Bridge — How to Use" (System Config)
