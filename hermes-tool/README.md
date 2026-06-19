# push_message Tool Integration

This directory contains the `push_message` Hermes tool that sends push notifications via the Notification Bridge.

## Files

| File | Purpose |
|------|---------|
| `push_message_tool.py` | Python module implementing the `push_message()` function |

## Usage

### As a Hermes Tool

The tool is registered in `hermes-agent/tools/registry.py` with:

```python
from hermes_notification_bridge.push_message_tool import push_message, PUSH_MESSAGE_SCHEMA

registry.register(
    name="push_message",
    toolset="hermes-notification-bridge",
    schema=PUSH_MESSAGE_SCHEMA,
    handler=lambda args, **kw: push_message(**args),
    check_fn=None,  # or implement availability check if needed
    emoji="🔔",
)
```

### Direct Import

```python
from hermes_notification_bridge.push_message_tool import push_message

result = push_message(
    title="Backup Failed",
    body="Daily backup failed due to Ollama rate limit.",
    priority="high",
    source="cron",
    voice=True,
)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_BASE_URL` | `http://notification-bridge:8655` | Base URL of the Notification Bridge |
| `BRIDGE_AUTH_KEY` | `hermes-bridge-dev-key` | Bearer token for authentication |
| `BRIDGE_TIMEOUT` | `10` | HTTP timeout in seconds |

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `title` | str | Yes | - | Notification title |
| `body` | str | Yes | - | Message body |
| `priority` | str | No | `normal` | low, normal, high, urgent |
| `source` | str | No | `system` | cron, orchestrator, voice, kanban, system, github-watcher, alert |
| `action_url` | str | No | - | URL to open when notification is clicked |
| `voice` | bool | No | False | Also deliver via voice assistant TTS |
| `tts_text` | str | No | - | Alternative text for TTS |
| `requires_reply` | bool | No | False | If True, user should respond |

## API Endpoint

The tool POSTs to: `{BRIDGE_BASE_URL}/push`

**Headers:**
- `Content-Type: application/json`
- `Authorization: Bearer {BRIDGE_AUTH_KEY}`

**Request Body:**
```json
{
  "title": "string",
  "body": "string",
  "priority": "low|normal|high|urgent",
  "source": "cron|orchestrator|voice|kanban|system|github-watcher|alert",
  "action_url": "string (optional)",
  "voice": boolean,
  "tts_text": "string (optional)",
  "requires_reply": boolean
}
```

## Integration with Hermes Profiles

Any Hermes profile (orchestrator, kanban-worker, cronjob, etc.) can call `push_message()`:

```python
# Orchestrator
push_message(
    title="Task Completed",
    body="Your daily report is ready.",
    source="orchestrator",
)

# Kanban worker
push_message(
    title="Review Required",
    body="PR #123 needs your attention.",
    priority="high",
    source="kanban",
    requires_reply=True,
)

# Cron job
push_message(
    title="Backup Failed",
    body="Daily backup failed after 3 retries.",
    priority="urgent",
    source="cron",
    voice=True,
)
```
