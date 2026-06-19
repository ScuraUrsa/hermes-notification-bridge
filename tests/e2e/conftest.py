"""E2E test harness for the Notification Bridge.

Tests the full flow: HTTP push → WebSocket delivery → verification.
No Android emulator required — uses Python WebSocket client instead.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import ClientSession, WSMsgType
from aiohttp.test_utils import unused_port

# Add repo root to path so bridge package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRIDGE_AUTH_KEY = "test-e2e-key-2026"
BRIDGE_HEARTBEAT = 3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def bridge_service(tmp_path):
    """Start a real Bridge service on a random port as a subprocess.

    Returns (port, base_url) tuple.
    """
    db_path = tmp_path / "e2e-messages.db"
    port = unused_port()

    env = os.environ.copy()
    env["BRIDGE_PORT"] = str(port)
    env["BRIDGE_HOST"] = "127.0.0.1"
    env["BRIDGE_AUTH_KEY"] = BRIDGE_AUTH_KEY
    env["BRIDGE_DB_PATH"] = str(db_path)
    env["BRIDGE_HEARTBEAT_INTERVAL"] = str(BRIDGE_HEARTBEAT)
    env["BRIDGE_MAX_MESSAGES"] = "100"

    repo_root = str(Path(__file__).parent.parent.parent)
    proc = subprocess.Popen(
        [sys.executable, "run_bridge.py"],
        env=env,
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base_url = f"http://127.0.0.1:{port}"
    # Wait for health check
    for _ in range(30):
        try:
            async with ClientSession() as session:
                async with session.get(f"{base_url}/health") as resp:
                    if resp.status == 200:
                        break
        except Exception:
            pass
        await asyncio.sleep(0.5)
    else:
        proc.terminate()
        proc.wait()
        pytest.fail("Bridge failed to start within 15 seconds")

    yield port, base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def auth_headers():
    """Authorization headers for Bridge push requests."""
    return {"Authorization": f"Bearer {BRIDGE_AUTH_KEY}"}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def bridge_push(session, base_url, title, body, headers, **kwargs):
    """POST /push to the Bridge. Returns (status, json_body)."""
    payload = {"title": title, "body": body, **kwargs}
    async with session.post(
        f"{base_url}/push", json=payload, headers=headers
    ) as resp:
        return resp.status, await resp.json()


async def bridge_get_messages(session, base_url, **params):
    """GET /messages from the Bridge. Returns (status, json_body)."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{base_url}/messages"
    if query:
        url += f"?{query}"
    async with session.get(url) as resp:
        return resp.status, await resp.json()


async def bridge_stats(session, base_url):
    """GET /stats from the Bridge. Returns (status, json_body)."""
    async with session.get(f"{base_url}/stats") as resp:
        return resp.status, await resp.json()


async def bridge_health(session, base_url):
    """GET /health from the Bridge. Returns (status, json_body)."""
    async with session.get(f"{base_url}/health") as resp:
        return resp.status, await resp.json()


async def ws_connect_and_collect(port, timeout=5):
    """Connect WebSocket to Bridge, collect all messages within timeout.

    Returns list of message data dicts (only 'message' events, not heartbeats).
    """
    messages = []
    async with ClientSession() as session:
        async with session.ws_connect(f"http://127.0.0.1:{port}/events/ws") as ws:
            try:
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("event") == "message":
                            messages.append(data["data"])
                    elif msg.type == WSMsgType.CLOSE:
                        break
            except asyncio.TimeoutError:
                pass
    return messages
