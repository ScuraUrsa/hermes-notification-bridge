"""Unit tests for the Notification Bridge service.

Tests all endpoints, WebSocket, SSE, SQLite persistence, and message cleanup.
Uses pytest + pytest-aiohttp. Starts a real Bridge instance in a fixture.
"""

import asyncio
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from aiohttp import web, ClientSession, WSMsgType
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# Add bridge package to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bridge.server import create_app
from bridge.config import BridgeConfig
from bridge.models import Message, MessageStore, init_db, get_connection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = get_connection(Path(db_path))
    init_db(conn)
    yield conn, db_path
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def msg_store(temp_db):
    """Create a MessageStore backed by a temp database."""
    conn, _ = temp_db
    return MessageStore(conn)


@pytest.fixture
def bridge_config():
    """Bridge config for testing."""
    return BridgeConfig()


@pytest.fixture
async def bridge_app(aiohttp_client, tmp_path):
    """Create a test Bridge application with a temp database."""
    db_path = tmp_path / "test-messages.db"
    os.environ["BRIDGE_DB_PATH"] = str(db_path)
    os.environ["BRIDGE_AUTH_KEY"] = "test-key"
    os.environ["BRIDGE_HEARTBEAT_INTERVAL"] = "5"
    os.environ["BRIDGE_MAX_MESSAGES"] = "50"

    # Re-import config to pick up env vars
    import importlib
    import bridge.config
    importlib.reload(bridge.config)

    app = create_app()
    return await aiohttp_client(app)


@pytest.fixture
def auth_headers():
    """Authorization headers for test requests."""
    return {"Authorization": "Bearer test-key"}


# ---------------------------------------------------------------------------
# Message model tests
# ---------------------------------------------------------------------------

class TestMessageModel:
    def test_create_message(self):
        msg = Message.create(
            source="test",
            title="Test Title",
            body="Test body content",
            priority="high",
        )
        assert msg.id is not None
        assert len(msg.id) == 36  # UUID
        assert msg.source == "test"
        assert msg.title == "Test Title"
        assert msg.body == "Test body content"
        assert msg.priority == "high"
        assert msg.type == "notification"
        assert msg.voice is False
        assert msg.read_at is None
        assert msg.created_at > 0

    def test_create_message_with_voice(self):
        msg = Message.create(
            source="voice",
            title="Voice msg",
            body="Read this aloud",
            voice=True,
            tts_text="Short version",
        )
        assert msg.voice is True
        assert msg.tts_text == "Short version"

    def test_to_dict(self):
        msg = Message.create(source="test", title="T", body="B")
        d = msg.to_dict()
        assert d["id"] == msg.id
        assert d["source"] == "test"
        assert d["title"] == "T"
        assert d["body"] == "B"
        assert "created_at" in d

    def test_to_push_event(self):
        msg = Message.create(source="test", title="T", body="B")
        event = msg.to_push_event()
        assert event["event"] == "message"
        assert event["data"]["id"] == msg.id
        assert event["data"]["title"] == "T"


# ---------------------------------------------------------------------------
# MessageStore tests
# ---------------------------------------------------------------------------

class TestMessageStore:
    def test_insert_and_get(self, msg_store):
        msg = Message.create(source="test", title="Hello", body="World")
        msg_store.insert(msg)
        retrieved = msg_store.get(msg.id)
        assert retrieved is not None
        assert retrieved.id == msg.id
        assert retrieved.body == "World"

    def test_get_nonexistent(self, msg_store):
        assert msg_store.get("nonexistent-id") is None

    def test_get_recent(self, msg_store):
        for i in range(5):
            msg = Message.create(source="test", title=f"Msg {i}", body=f"Body {i}")
            msg_store.insert(msg)
            time.sleep(0.01)  # ensure different timestamps

        recent = msg_store.get_recent(limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0].title == "Msg 4"

    def test_get_recent_with_offset(self, msg_store):
        for i in range(5):
            msg = Message.create(source="test", title=f"Msg {i}", body=f"Body {i}")
            msg_store.insert(msg)
            time.sleep(0.01)

        recent = msg_store.get_recent(limit=2, offset=2)
        assert len(recent) == 2
        assert recent[0].title == "Msg 2"

    def test_get_recent_voice_only(self, msg_store):
        msg_store.insert(Message.create(source="test", title="Normal", body="B"))
        msg_store.insert(Message.create(source="voice", title="Voice", body="VB", voice=True))

        voice_msgs = msg_store.get_recent(voice_only=True)
        assert len(voice_msgs) == 1
        assert voice_msgs[0].title == "Voice"

    def test_get_unread(self, msg_store):
        msg1 = Message.create(source="test", title="Unread", body="B")
        msg2 = Message.create(source="test", title="Read", body="B")
        msg_store.insert(msg1)
        msg_store.insert(msg2)
        msg_store.mark_read(msg2.id)

        unread = msg_store.get_unread()
        assert len(unread) == 1
        assert unread[0].id == msg1.id

    def test_mark_read(self, msg_store):
        msg = Message.create(source="test", title="T", body="B")
        msg_store.insert(msg)
        assert msg_store.mark_read(msg.id) is True
        # Second mark_read should return False (already read)
        assert msg_store.mark_read(msg.id) is False

    def test_mark_read_nonexistent(self, msg_store):
        assert msg_store.mark_read("nonexistent") is False

    def test_record_delivery(self, msg_store):
        msg = Message.create(source="test", title="T", body="B")
        msg_store.insert(msg)
        msg_store.record_delivery(msg.id, "client-1")
        msg_store.record_delivery(msg.id, "client-2")
        msg_store.record_delivery(msg.id, "client-1")  # duplicate

        retrieved = msg_store.get(msg.id)
        assert "client-1" in retrieved.delivered_to
        assert "client-2" in retrieved.delivered_to
        assert len(retrieved.delivered_to) == 2

    def test_get_stats(self, msg_store):
        msg_store.insert(Message.create(source="test", title="T1", body="B"))
        msg_store.insert(Message.create(source="voice", title="T2", body="B", voice=True))

        stats = msg_store.get_stats()
        assert stats["total"] == 2
        assert stats["unread"] == 2
        assert stats["voice"] == 1

    def test_cleanup_old(self, msg_store):
        # Insert 60 messages, max is 50
        for i in range(60):
            msg_store.insert(Message.create(source="test", title=f"Msg {i}", body=f"B{i}"))
            time.sleep(0.01)

        msg_store.cleanup_old(max_messages=50)
        stats = msg_store.get_stats()
        assert stats["total"] == 50


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    async def test_health_ok(self, bridge_app):
        resp = await bridge_app.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "hermes-notification-bridge"


class TestPushEndpoint:
    async def test_push_success(self, bridge_app, auth_headers):
        payload = {
            "title": "Test Notification",
            "body": "This is a test message",
            "source": "test",
            "priority": "high",
        }
        resp = await bridge_app.post("/push", json=payload, headers=auth_headers)
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "delivered"
        assert "id" in data

    async def test_push_unauthorized(self, bridge_app):
        payload = {"title": "Test", "body": "Test"}
        resp = await bridge_app.post("/push", json=payload)
        assert resp.status == 401

    async def test_push_missing_body(self, bridge_app, auth_headers):
        payload = {"title": "No body"}
        resp = await bridge_app.post("/push", json=payload, headers=auth_headers)
        assert resp.status == 400

    async def test_push_invalid_json(self, bridge_app, auth_headers):
        resp = await bridge_app.post("/push", data="not json", headers=auth_headers)
        assert resp.status == 400

    async def test_push_with_voice(self, bridge_app, auth_headers):
        payload = {
            "title": "Voice msg",
            "body": "Read aloud",
            "voice": True,
            "tts_text": "Short",
        }
        resp = await bridge_app.post("/push", json=payload, headers=auth_headers)
        assert resp.status == 201

    async def test_push_with_action(self, bridge_app, auth_headers):
        payload = {
            "title": "Action msg",
            "body": "Click me",
            "action": {"type": "open_url", "url": "http://example.com"},
        }
        resp = await bridge_app.post("/push", json=payload, headers=auth_headers)
        assert resp.status == 201


class TestMessagesEndpoint:
    async def test_get_messages_empty(self, bridge_app):
        resp = await bridge_app.get("/messages")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data, list)
        assert len(data) == 0

    async def test_get_messages_after_push(self, bridge_app, auth_headers):
        # Push a message first
        payload = {"title": "M1", "body": "Body 1"}
        await bridge_app.post("/push", json=payload, headers=auth_headers)

        resp = await bridge_app.get("/messages")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "M1"

    async def test_get_messages_with_limit(self, bridge_app, auth_headers):
        for i in range(5):
            payload = {"title": f"M{i}", "body": f"Body {i}"}
            await bridge_app.post("/push", json=payload, headers=auth_headers)

        resp = await bridge_app.get("/messages?limit=2")
        data = await resp.json()
        assert len(data) == 2

    async def test_get_messages_voice_filter(self, bridge_app, auth_headers):
        await bridge_app.post("/push", json={"title": "Normal", "body": "B"}, headers=auth_headers)
        await bridge_app.post("/push", json={"title": "Voice", "body": "VB", "voice": True}, headers=auth_headers)

        resp = await bridge_app.get("/messages?voice=true")
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Voice"

    async def test_get_messages_since_filter(self, bridge_app, auth_headers):
        await bridge_app.post("/push", json={"title": "Old", "body": "B"}, headers=auth_headers)
        ts_mid = int(time.time())
        await bridge_app.post("/push", json={"title": "New", "body": "B"}, headers=auth_headers)

        resp = await bridge_app.get(f"/messages?since={ts_mid}")
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "New"


class TestMessageGetEndpoint:
    async def test_get_existing_message(self, bridge_app, auth_headers):
        push_resp = await bridge_app.post(
            "/push", json={"title": "T", "body": "B"}, headers=auth_headers
        )
        msg_id = (await push_resp.json())["id"]

        resp = await bridge_app.get(f"/messages/{msg_id}")
        assert resp.status == 200
        data = await resp.json()
        assert data["id"] == msg_id

    async def test_get_nonexistent_message(self, bridge_app):
        resp = await bridge_app.get("/messages/nonexistent")
        assert resp.status == 404


class TestMessageReadEndpoint:
    async def test_mark_read(self, bridge_app, auth_headers):
        push_resp = await bridge_app.post(
            "/push", json={"title": "T", "body": "B"}, headers=auth_headers
        )
        msg_id = (await push_resp.json())["id"]

        resp = await bridge_app.post(f"/messages/{msg_id}/read")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "marked_read"

    async def test_mark_read_twice(self, bridge_app, auth_headers):
        push_resp = await bridge_app.post(
            "/push", json={"title": "T", "body": "B"}, headers=auth_headers
        )
        msg_id = (await push_resp.json())["id"]

        await bridge_app.post(f"/messages/{msg_id}/read")
        resp = await bridge_app.post(f"/messages/{msg_id}/read")
        assert resp.status == 404


class TestStatsEndpoint:
    async def test_stats(self, bridge_app, auth_headers):
        await bridge_app.post("/push", json={"title": "T", "body": "B"}, headers=auth_headers)

        resp = await bridge_app.get("/stats")
        assert resp.status == 200
        data = await resp.json()
        assert data["total"] == 1
        assert data["unread"] == 1
        assert "connected_clients" in data


class TestLastDeliveryEndpoint:
    async def test_last_delivery_empty(self, bridge_app):
        resp = await bridge_app.get("/test/last-delivery")
        data = await resp.json()
        assert data["status"] == "no_messages"

    async def test_last_delivery_after_push(self, bridge_app, auth_headers):
        await bridge_app.post(
            "/push", json={"title": "Last", "body": "Last body"}, headers=auth_headers
        )

        resp = await bridge_app.get("/test/last-delivery")
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["message"]["title"] == "Last"


# ---------------------------------------------------------------------------
# WebSocket tests
# ---------------------------------------------------------------------------

class TestWebSocket:
    async def test_ws_connect(self, bridge_app, auth_headers):
        """WebSocket client connects and receives broadcast messages."""
        async with bridge_app.server.app["client_session"] as session:
            # Connect via WebSocket
            ws_url = str(bridge_app.make_url("/events/ws")).replace("http://", "ws://")
            async with session.ws_connect(ws_url) as ws:
                # Push a message
                await bridge_app.post(
                    "/push",
                    json={"title": "WS Test", "body": "Hello via WS"},
                    headers=auth_headers,
                )

                # Receive the broadcast
                msg = await ws.receive()
                assert msg.type == WSMsgType.TEXT
                data = json.loads(msg.data)
                assert data["event"] == "message"
                assert data["data"]["title"] == "WS Test"

    async def test_ws_heartbeat(self, bridge_app):
        """WebSocket client receives heartbeats."""
        async with bridge_app.server.app["client_session"] as session:
            ws_url = str(bridge_app.make_url("/events/ws")).replace("http://", "ws://")
            async with session.ws_connect(ws_url) as ws:
                # Wait for heartbeat (interval is 5s in test config)
                msg = await asyncio.wait_for(ws.receive(), timeout=10)
                data = json.loads(msg.data)
                assert data["event"] == "heartbeat"


# ---------------------------------------------------------------------------
# SSE tests
# ---------------------------------------------------------------------------

class TestSSE:
    async def test_sse_connect_and_receive(self, bridge_app, auth_headers):
        """SSE client connects and receives broadcast messages."""
        async with bridge_app.server.app["client_session"] as session:
            async with session.get(
                str(bridge_app.make_url("/events")),
                headers={"Accept": "text/event-stream"},
            ) as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"] == "text/event-stream"

                # Push a message
                await bridge_app.post(
                    "/push",
                    json={"title": "SSE Test", "body": "Hello via SSE"},
                    headers=auth_headers,
                )

                # Read SSE data
                buffer = b""
                while True:
                    chunk = await resp.content.read(1024)
                    if not chunk:
                        break
                    buffer += chunk
                    if b"\\n\\n" in buffer:
                        break

                text = buffer.decode()
                assert "event: message" in text
                assert "SSE Test" in text


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------

class TestPersistence:
    async def test_messages_survive_restart(self, bridge_app, auth_headers, tmp_path):
        """Messages persist in SQLite across app restarts."""
        # Push a message
        await bridge_app.post(
            "/push", json={"title": "Persistent", "body": "Survive restart"}, headers=auth_headers
        )

        # Verify it's there
        resp = await bridge_app.get("/messages")
        data = await resp.json()
        assert len(data) == 1
        msg_id = data[0]["id"]

        # Create a new app instance (simulating restart) with same DB
        import importlib
        import bridge.config
        importlib.reload(bridge.config)

        app2 = create_app()
        client2 = await aiohttp_client(app2)

        # Message should still be there
        resp2 = await client2.get(f"/messages/{msg_id}")
        assert resp2.status == 200
        data2 = await resp2.json()
        assert data2["title"] == "Persistent"
