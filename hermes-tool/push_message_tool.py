"""
Push Message Tool — send notifications from Hermes to the user via the Notification Bridge.

POSTs to the Notification Bridge service (port 8655) which then delivers
the message to connected clients (Conduit, Open WebUI) via WebSocket/SSE.
"""

import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Default Bridge URL — configurable via env var
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://notification-bridge:8655")
BRIDGE_AUTH_KEY = os.environ.get("BRIDGE_AUTH_KEY", "hermes-bridge-dev-key")

VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_SOURCES = {"cron", "orchestrator", "voice", "kanban", "system", "github-watcher", "alert"}


def push_message(
    title: str,
    body: str,
    priority: str = "normal",
    source: str = "system",
    action_url: str = "",
    voice: bool = False,
    tts_text: str = "",
    requires_reply: bool = False,
) -> str:
    """Send a push notification to the user via the Notification Bridge.

    Args:
        title: Notification title.
        body: Message body (required).
        priority: low, normal, high, or urgent (default: normal).
        source: Where the message originated (default: system).
        action_url: URL to open when notification is clicked.
        voice: Also deliver via voice assistant TTS (default: False).
        tts_text: Alternative text for TTS (if different from body).
        requires_reply: If True, user should respond (default: False).

    Returns:
        JSON string with delivery status.
    """
    if not body or not body.strip():
        return json.dumps({"error": "body is required"})

    if priority not in VALID_PRIORITIES:
        return json.dumps({"error": f"Invalid priority '{priority}'. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}"})

    if source not in VALID_SOURCES:
        return json.dumps({"error": f"Invalid source '{source}'. Must be one of: {', '.join(sorted(VALID_SOURCES))}"})

    payload = {
        "title": title.strip() if title else "",
        "body": body.strip(),
        "priority": priority,
        "source": source,
        "type": "notification",
        "voice": voice,
        "tts_text": tts_text.strip() if tts_text else None,
        "metadata": {"requires_reply": requires_reply},
    }

    if action_url:
        payload["action"] = {
            "type": "open_url",
            "url": action_url,
        }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{BRIDGE_URL}/push",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BRIDGE_AUTH_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return json.dumps(result)
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8")
            error_data = json.loads(body_text)
        except Exception:
            error_data = {"error": f"HTTP {e.code}: {e.reason}"}
        return json.dumps({"error": f"Bridge returned {e.code}", "detail": error_data})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"Cannot reach Notification Bridge at {BRIDGE_URL}: {e.reason}"})
    except Exception as e:
        logger.exception("push_message tool error")
        return json.dumps({"error": f"push_message failed: {type(e).__name__}: {e}"})


def _check_push_message() -> bool:
    """Check if the Notification Bridge is reachable."""
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"
    except Exception:
        return False


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

PUSH_MESSAGE_SCHEMA = {
    "name": "push_message",
    "description": (
        "Send a push notification/message to the user via the Notification Bridge. "
        "The message appears in Conduit (mobile) and Open WebUI (browser) in real-time. "
        "Use this to proactively notify the user about completed tasks, failures, "
        "alerts, or any information that needs the user's attention."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Notification title",
            },
            "body": {
                "type": "string",
                "description": "Message body — the main content of the notification",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "urgent"],
                "description": "Priority level (default: normal). Urgent notifications may trigger immediate delivery.",
                "default": "normal",
            },
            "source": {
                "type": "string",
                "enum": ["cron", "orchestrator", "voice", "kanban", "system", "github-watcher", "alert"],
                "description": "Where the message originated (default: system)",
                "default": "system",
            },
            "action_url": {
                "type": "string",
                "description": "URL to open when the notification is clicked (e.g., a specific chat or dashboard)",
            },
            "voice": {
                "type": "boolean",
                "description": "Also deliver via voice assistant TTS (default: false)",
                "default": False,
            },
            "tts_text": {
                "type": "string",
                "description": "Alternative text for TTS (if different from body — e.g., shorter version for voice)",
            },
            "requires_reply": {
                "type": "boolean",
                "description": "If true, the user should respond to this message (default: false)",
                "default": False,
            },
        },
        "required": ["title", "body"],
    },
}


# --- Registry ---
from tools.registry import registry

registry.register(
    name="push_message",
    toolset="messaging",
    schema=PUSH_MESSAGE_SCHEMA,
    handler=lambda args, **kw: push_message(
        title=args.get("title", ""),
        body=args.get("body", ""),
        priority=args.get("priority", "normal"),
        source=args.get("source", "system"),
        action_url=args.get("action_url", ""),
        voice=args.get("voice", False),
        tts_text=args.get("tts_text", ""),
        requires_reply=args.get("requires_reply", False),
    ),
    check_fn=_check_push_message,
    emoji="🔔",
)
