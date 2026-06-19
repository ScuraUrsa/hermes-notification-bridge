#!/usr/bin/env python3
"""
Wyoming Voice Satellite — Bridge Poller

Polls the Notification Bridge for voice-flagged messages and feeds them
to the Wyoming voice pipeline for TTS output.

Intended to run on the Android phone (Termux) or alongside the Wyoming
satellite on the VM. Polls GET /messages?voice=true&since=<last_ts>
every N seconds, outputs new messages to stdout for the TTS pipeline.

Usage:
    python3 wyoming_bridge_poller.py [--bridge-url URL] [--interval SECONDS]

Environment:
    BRIDGE_URL — base URL of Notification Bridge (default: http://localhost:8655)
    BRIDGE_AUTH_KEY — auth key for Bridge (optional, for /push endpoint)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_BRIDGE_URL = "http://localhost:8655"
DEFAULT_POLL_INTERVAL = 5  # seconds
STATE_FILE = Path.home() / ".hermes" / "voice" / "bridge_poller_state.json"


def load_state():
    """Load last processed timestamp from state file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_ts": 0, "processed_ids": []}


def save_state(state):
    """Save state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def fetch_voice_messages(bridge_url, since_ts):
    """Fetch voice-flagged messages from the Bridge since given timestamp."""
    url = f"{bridge_url}/messages?voice=true&since={since_ts}&limit=50"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[bridge-poller] Connection error: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[bridge-poller] Error: {e}", file=sys.stderr)
        return []


def push_voice_reply(bridge_url, auth_key, reply_text, original_msg_id):
    """Push a voice reply back to the Bridge (user's spoken response)."""
    if not auth_key:
        print("[bridge-poller] No auth key — cannot push reply", file=sys.stderr)
        return False

    payload = json.dumps({
        "title": "Voice Reply",
        "body": reply_text,
        "source": "voice",
        "voice": True,
        "metadata": {"reply_to": original_msg_id, "requires_reply": False},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{bridge_url}/push",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("status") == "delivered"
    except Exception as e:
        print(f"[bridge-poller] Reply push failed: {e}", file=sys.stderr)
        return False


def format_for_tts(message):
    """Format a Bridge message for TTS output.

    Returns a plain-text string suitable for piping to a TTS engine.
    """
    tts_text = message.get("tts_text") or message.get("body", "")
    title = message.get("title", "")

    if title and title not in tts_text:
        return f"{title}. {tts_text}"
    return tts_text


def main():
    parser = argparse.ArgumentParser(
        description="Wyoming Bridge Poller — fetches voice messages from Notification Bridge"
    )
    parser.add_argument(
        "--bridge-url",
        default=os.environ.get("BRIDGE_URL", DEFAULT_BRIDGE_URL),
        help="Notification Bridge base URL",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL", DEFAULT_POLL_INTERVAL)),
        help="Poll interval in seconds",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit (for cron/testing)",
    )
    parser.add_argument(
        "--auth-key",
        default=os.environ.get("BRIDGE_AUTH_KEY", ""),
        help="Bridge auth key for pushing replies",
    )
    args = parser.parse_args()

    bridge_url = args.bridge_url.rstrip("/")
    state = load_state()
    print(f"[bridge-poller] Starting. Bridge: {bridge_url}, interval: {args.interval}s",
          file=sys.stderr)

    while True:
        try:
            messages = fetch_voice_messages(bridge_url, state["last_ts"])

            for msg in messages:
                msg_id = msg["id"]
                if msg_id in state["processed_ids"]:
                    continue

                tts_output = format_for_tts(msg)
                # Output to stdout — the Wyoming pipeline reads this
                print(tts_output, flush=True)

                # Log to stderr for diagnostics
                print(f"[bridge-poller] Voice message: {msg['title']} (id={msg_id})",
                      file=sys.stderr)

                state["processed_ids"].append(msg_id)
                # Keep only last 100 IDs to avoid unbounded growth
                if len(state["processed_ids"]) > 100:
                    state["processed_ids"] = state["processed_ids"][-100:]

                # Update last_ts to the latest message timestamp
                msg_ts = msg.get("created_at", 0)
                if msg_ts > state["last_ts"]:
                    state["last_ts"] = msg_ts

            save_state(state)

        except KeyboardInterrupt:
            print("[bridge-poller] Stopped.", file=sys.stderr)
            break
        except Exception as e:
            print(f"[bridge-poller] Poll error: {e}", file=sys.stderr)

        if args.once:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
