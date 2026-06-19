"""E2E tests for the Notification Bridge.

Tests the full flow: HTTP push → WebSocket delivery → verification.
Uses aiohttp AppRunner (same as unit tests) but with explicit
WebSocket client verification — this is the GATE TEST pattern.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from aiohttp import web, ClientSession, WSMsgType
from aiohttp.test_utils import unused_port

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import bridge.config
import bridge.models
import bridge.ws_manager
import bridge.server


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge_app(tmp_path):
    """Create a test Bridge app with unique DB per test."""
    import uuid
    db_path = tmp_path / f"e2e-{uuid.uuid4().hex[:8]}.db"
    os.environ["BRIDGE_DB_PATH"] = str(db_path)
    os.environ["BRIDGE_AUTH_KEY"] = "test-e2e-key"
    os.environ["BRIDGE_HEARTBEAT_INTERVAL"] = "3"
    os.environ["BRIDGE_MAX_MESSAGES"] = "100"

    import importlib
    importlib.reload(bridge.config)
    importlib.reload(bridge.models)
    importlib.reload(bridge.ws_manager)
    importlib.reload(bridge.server)

    app = bridge.server.create_app()
    asyncio.run(bridge.server.on_startup(app))
    port = unused_port()
    return app, port


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-e2e-key"}


# ---------------------------------------------------------------------------
# Helper: start app, return (base_url, runner) for cleanup
# ---------------------------------------------------------------------------

async def start_app(app, port):
    """Start the aiohttp app on localhost:port. Returns (base_url, runner)."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()
    return f"http://localhost:{port}", runner


# ---------------------------------------------------------------------------
# GATE TEST: Basic Push Delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_basic_push_delivery(bridge_app, auth_headers):
    """GATE TEST: Push a message, verify WebSocket delivery."""
    app, port = bridge_app
    base_url, runner = await start_app(app, port)

    try:
        async with ClientSession() as session:
            # 1. Health check
            async with session.get(f"{base_url}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            # 2. No messages yet
            async with session.get(f"{base_url}/messages") as resp:
                data = await resp.json()
                assert len(data) == 0

            # 3. Connect WebSocket
            ws_url = f"http://localhost:{port}/events/ws"
            async with session.ws_connect(ws_url) as ws:
                # 4. Push a message
                t0 = time.monotonic()
                async with session.post(
                    f"{base_url}/push",
                    json={
                        "title": "GATE TEST",
                        "body": "This message proves bidirectional messaging works end-to-end.",
                        "source": "test",
                        "priority": "high",
                    },
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201
                    push_result = await resp.json()
                    assert push_result["status"] == "delivered"
                    msg_id = push_result["id"]

                # 5. Receive via WebSocket
                ws_msg = await asyncio.wait_for(ws.receive(), timeout=5)
                t1 = time.monotonic()

                assert ws_msg.type == WSMsgType.TEXT
                ws_data = json.loads(ws_msg.data)
                assert ws_data["event"] == "message"
                assert ws_data["data"]["title"] == "GATE TEST"
                assert ws_data["data"]["body"] == "This message proves bidirectional messaging works end-to-end."
                assert ws_data["data"]["id"] == msg_id
                assert ws_data["data"]["source"] == "test"
                assert ws_data["data"]["priority"] == "high"

                # 6. Latency < 2 seconds
                assert (t1 - t0) < 2.0, f"Delivery latency {t1 - t0:.2f}s exceeds 2s limit"

            # 7. Verify stats
            async with session.get(f"{base_url}/stats") as resp:
                stats = await resp.json()
                assert stats["total"] == 1
                assert stats["unread"] == 1

            # 8. Verify REST retrieval
            async with session.get(f"{base_url}/messages") as resp:
                msgs = await resp.json()
                assert len(msgs) == 1
                assert msgs[0]["id"] == msg_id
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Multiple messages in sequence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_messages_sequentially(bridge_app, auth_headers):
    """Push 5 messages, verify all received in order via WebSocket."""
    app, port = bridge_app
    base_url, runner = await start_app(app, port)

    try:
        async with ClientSession() as session:
            async with session.ws_connect(f"http://localhost:{port}/events/ws") as ws:
                sent_titles = []
                for i in range(5):
                    title = f"Message {i+1}"
                    sent_titles.append(title)
                    async with session.post(
                        f"{base_url}/push",
                        json={"title": title, "body": f"Body {i+1}"},
                        headers=auth_headers,
                    ) as resp:
                        assert resp.status == 201
                    await asyncio.sleep(0.05)

                # Collect all 5 messages
                received = []
                for _ in range(5):
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("event") == "message":
                            received.append(data["data"]["title"])

                assert received == sent_titles, f"Order mismatch: {received} != {sent_titles}"

            async with session.get(f"{base_url}/stats") as resp:
                stats = await resp.json()
                assert stats["total"] == 5
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Reconnection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_websocket_reconnection(bridge_app, auth_headers):
    """New WebSocket client receives messages after previous one disconnects."""
    app, port = bridge_app
    base_url, runner = await start_app(app, port)

    try:
        async with ClientSession() as session:
            # First client connects, receives one message, disconnects
            async with session.ws_connect(f"http://localhost:{port}/events/ws") as ws1:
                async with session.post(
                    f"{base_url}/push",
                    json={"title": "First", "body": "For client 1"},
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201

                msg = await asyncio.wait_for(ws1.receive(), timeout=5)
                data = json.loads(msg.data)
                assert data["data"]["title"] == "First"

            # Second client connects, receives new message
            async with session.ws_connect(f"http://localhost:{port}/events/ws") as ws2:
                async with session.post(
                    f"{base_url}/push",
                    json={"title": "Second", "body": "For client 2"},
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201

                msg = await asyncio.wait_for(ws2.receive(), timeout=5)
                data = json.loads(msg.data)
                assert data["data"]["title"] == "Second"
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Concurrent clients
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_clients(bridge_app, auth_headers):
    """Two WebSocket clients both receive the same broadcast."""
    app, port = bridge_app
    base_url, runner = await start_app(app, port)

    try:
        async with ClientSession() as session:
            async with session.ws_connect(f"http://localhost:{port}/events/ws") as ws1, \
                       session.ws_connect(f"http://localhost:{port}/events/ws") as ws2:

                async with session.post(
                    f"{base_url}/push",
                    json={"title": "Broadcast", "body": "For everyone"},
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201

                msg1 = await asyncio.wait_for(ws1.receive(), timeout=5)
                msg2 = await asyncio.wait_for(ws2.receive(), timeout=5)

                data1 = json.loads(msg1.data)
                data2 = json.loads(msg2.data)
                assert data1["data"]["title"] == "Broadcast"
                assert data2["data"]["title"] == "Broadcast"
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Voice message delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_message_delivery(bridge_app, auth_headers):
    """Voice-flagged messages are delivered and filterable."""
    app, port = bridge_app
    base_url, runner = await start_app(app, port)

    try:
        async with ClientSession() as session:
            # Push normal
            async with session.post(
                f"{base_url}/push",
                json={"title": "Normal", "body": "Regular"},
                headers=auth_headers,
            ) as resp:
                assert resp.status == 201

            # Push voice
            async with session.post(
                f"{base_url}/push",
                json={"title": "Voice", "body": "Read aloud", "voice": True, "tts_text": "Short"},
                headers=auth_headers,
            ) as resp:
                assert resp.status == 201

            # Voice filter
            async with session.get(f"{base_url}/messages?voice=true") as resp:
                msgs = await resp.json()
                assert len(msgs) == 1
                assert msgs[0]["title"] == "Voice"
                assert msgs[0]["voice"] is True

            # All messages
            async with session.get(f"{base_url}/messages") as resp:
                msgs = await resp.json()
                assert len(msgs) == 2
    finally:
        await runner.cleanup()
