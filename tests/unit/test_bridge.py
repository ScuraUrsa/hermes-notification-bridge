"""Unit tests for the Notification Bridge service.

Tests all endpoints, WebSocket, SSE, SQLite persistence, and message cleanup.
Uses pytest + pytest-aiohttp. Starts a real Bridge instance in a fixture.
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from aiohttp import web, ClientSession, WSMsgType
from aiohttp.test_utils import unused_port

# Add bridge package to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bridge.server import create_app
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
def bridge_app(tmp_path):
    """Create a test Bridge application with initialized stores.

    Returns (app, port) tuple. Tests use this to create their own test client.
    Each test gets a unique DB to avoid cross-test contamination.
    """
    import uuid
    db_path = tmp_path / f"test-{uuid.uuid4().hex[:8]}.db"
    os.environ["BRIDGE_DB_PATH"] = str(db_path)
    os.environ["BRIDGE_AUTH_KEY"] = "test-key"
    os.environ["BRIDGE_HEARTBEAT_INTERVAL"] = "5"
    os.environ["BRIDGE_MAX_MESSAGES"] = "50"

    import importlib
    import bridge.config
    import bridge.server
    importlib.reload(bridge.config)
    importlib.reload(bridge.server)

    app = bridge.server.create_app()
    # Manually run on_startup since AppRunner doesn't trigger lifecycle hooks
    asyncio.run(bridge.server.on_startup(app))
    port = unused_port()
    return app, port


@pytest.fixture
def auth_headers():
    """Authorization headers for test requests."""
    return {"Authorization": "Bearer test-key"}


# ---------------------------------------------------------------------------
# Message model tests (sync — no bridge_app needed)
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
# MessageStore tests (sync — no bridge_app needed)
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
        base_ts = int(time.time())
        for i in range(5):
            msg = Message.create(source="test", title=f"Msg {i}", body=f"Body {i}")
            msg.created_at = base_ts + i
            msg_store.insert(msg)

        recent = msg_store.get_recent(limit=3)
        assert len(recent) == 3
        assert recent[0].title == "Msg 4"

    def test_get_recent_with_offset(self, msg_store):
        base_ts = int(time.time())
        for i in range(5):
            msg = Message.create(source="test", title=f"Msg {i}", body=f"Body {i}")
            msg.created_at = base_ts + i
            msg_store.insert(msg)

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
        assert msg_store.mark_read(msg.id) is False

    def test_mark_read_nonexistent(self, msg_store):
        assert msg_store.mark_read("nonexistent") is False

    def test_record_delivery(self, msg_store):
        msg = Message.create(source="test", title="T", body="B")
        msg_store.insert(msg)
        msg_store.record_delivery(msg.id, "client-1")
        msg_store.record_delivery(msg.id, "client-2")
        msg_store.record_delivery(msg.id, "client-1")

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
        base_ts = int(time.time())
        for i in range(60):
            msg = Message.create(source="test", title=f"Msg {i}", body=f"B{i}")
            msg.created_at = base_ts + i
            msg_store.insert(msg)

        msg_store.cleanup_old(max_messages=50)
        stats = msg_store.get_stats()
        assert stats["total"] == 50


# ---------------------------------------------------------------------------
# HTTP endpoint tests (async — use bridge_app fixture)
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    async def test_health_ok(self, bridge_app):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.get(f"http://localhost:{port}/health") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "ok"
                    assert data["service"] == "hermes-notification-bridge"
        finally:
            await runner.cleanup()


class TestPushEndpoint:
    async def test_push_success(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                payload = {
                    "title": "Test Notification",
                    "body": "This is a test message",
                    "source": "test",
                    "priority": "high",
                }
                async with session.post(
                    f"http://localhost:{port}/push",
                    json=payload,
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201
                    data = await resp.json()
                    assert data["status"] == "delivered"
                    assert "id" in data
        finally:
            await runner.cleanup()

    async def test_push_unauthorized(self, bridge_app):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                payload = {"title": "Test", "body": "Test"}
                async with session.post(
                    f"http://localhost:{port}/push", json=payload
                ) as resp:
                    assert resp.status == 401
        finally:
            await runner.cleanup()

    async def test_push_missing_body(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                payload = {"title": "No body"}
                async with session.post(
                    f"http://localhost:{port}/push",
                    json=payload,
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 400
        finally:
            await runner.cleanup()

    async def test_push_invalid_json(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    data="not json",
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 400
        finally:
            await runner.cleanup()

    async def test_push_with_voice(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                payload = {
                    "title": "Voice msg",
                    "body": "Read aloud",
                    "voice": True,
                    "tts_text": "Short",
                }
                async with session.post(
                    f"http://localhost:{port}/push",
                    json=payload,
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201
        finally:
            await runner.cleanup()

    async def test_push_with_action(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                payload = {
                    "title": "Action msg",
                    "body": "Click me",
                    "action": {"type": "open_url", "url": "http://example.com"},
                }
                async with session.post(
                    f"http://localhost:{port}/push",
                    json=payload,
                    headers=auth_headers,
                ) as resp:
                    assert resp.status == 201
        finally:
            await runner.cleanup()


class TestMessagesEndpoint:
    async def test_get_messages_empty(self, bridge_app):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.get(f"http://localhost:{port}/messages") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert isinstance(data, list)
                    assert len(data) == 0
        finally:
            await runner.cleanup()

    async def test_get_messages_after_push(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                payload = {"title": "M1", "body": "Body 1"}
                async with session.post(
                    f"http://localhost:{port}/push",
                    json=payload,
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(f"http://localhost:{port}/messages") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert len(data) == 1
                    assert data[0]["title"] == "M1"
        finally:
            await runner.cleanup()

    async def test_get_messages_with_limit(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                for i in range(5):
                    payload = {"title": f"M{i}", "body": f"Body {i}"}
                    async with session.post(
                        f"http://localhost:{port}/push",
                        json=payload,
                        headers=auth_headers,
                    ) as _:
                        pass

                async with session.get(f"http://localhost:{port}/messages?limit=2") as resp:
                    data = await resp.json()
                    assert len(data) == 2
        finally:
            await runner.cleanup()

    async def test_get_messages_voice_filter(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "Normal", "body": "B"},
                    headers=auth_headers,
                ) as _:
                    pass
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "Voice", "body": "VB", "voice": True},
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(f"http://localhost:{port}/messages?voice=true") as resp:
                    data = await resp.json()
                    assert len(data) == 1
                    assert data[0]["title"] == "Voice"
        finally:
            await runner.cleanup()

    async def test_get_messages_since_filter(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "Old", "body": "B"},
                    headers=auth_headers,
                ) as _:
                    pass
                ts_mid = int(time.time())
                await asyncio.sleep(0.1)
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "New", "body": "B"},
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(f"http://localhost:{port}/messages?since={ts_mid}") as resp:
                    data = await resp.json()
                    assert len(data) == 1
                    assert data[0]["title"] == "New"
        finally:
            await runner.cleanup()


class TestMessageGetEndpoint:
    async def test_get_existing_message(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "T", "body": "B"},
                    headers=auth_headers,
                ) as push_resp:
                    msg_id = (await push_resp.json())["id"]

                async with session.get(f"http://localhost:{port}/messages/{msg_id}") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["id"] == msg_id
        finally:
            await runner.cleanup()

    async def test_get_nonexistent_message(self, bridge_app):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.get(f"http://localhost:{port}/messages/nonexistent") as resp:
                    assert resp.status == 404
        finally:
            await runner.cleanup()


class TestMessageReadEndpoint:
    async def test_mark_read(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "T", "body": "B"},
                    headers=auth_headers,
                ) as push_resp:
                    msg_id = (await push_resp.json())["id"]

                async with session.post(f"http://localhost:{port}/messages/{msg_id}/read") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "marked_read"
        finally:
            await runner.cleanup()

    async def test_mark_read_twice(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "T", "body": "B"},
                    headers=auth_headers,
                ) as push_resp:
                    msg_id = (await push_resp.json())["id"]

                async with session.post(f"http://localhost:{port}/messages/{msg_id}/read") as _:
                    pass
                async with session.post(f"http://localhost:{port}/messages/{msg_id}/read") as resp:
                    assert resp.status == 404
        finally:
            await runner.cleanup()


class TestStatsEndpoint:
    async def test_stats(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "T", "body": "B"},
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(f"http://localhost:{port}/stats") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["total"] == 1
                    assert data["unread"] == 1
                    assert "connected_clients" in data
        finally:
            await runner.cleanup()


class TestLastDeliveryEndpoint:
    async def test_last_delivery_empty(self, bridge_app):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.get(f"http://localhost:{port}/test/last-delivery") as resp:
                    data = await resp.json()
                    assert data["status"] == "no_messages"
        finally:
            await runner.cleanup()

    async def test_last_delivery_after_push(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "Last", "body": "Last body"},
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(f"http://localhost:{port}/test/last-delivery") as resp:
                    data = await resp.json()
                    assert data["status"] == "ok"
                    assert data["message"]["title"] == "Last"
        finally:
            await runner.cleanup()


# ---------------------------------------------------------------------------
# WebSocket tests
# ---------------------------------------------------------------------------

class TestWebSocket:
    async def test_ws_connect(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                # Push a message while connected
                async with session.ws_connect(f"http://localhost:{port}/events/ws") as ws:
                    async with session.post(
                        f"http://localhost:{port}/push",
                        json={"title": "WS Test", "body": "Hello via WS"},
                        headers=auth_headers,
                    ) as _:
                        pass

                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    assert msg.type == WSMsgType.TEXT
                    data = json.loads(msg.data)
                    assert data["event"] == "message"
                    assert data["data"]["title"] == "WS Test"
        finally:
            await runner.cleanup()

    async def test_ws_heartbeat(self, bridge_app):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://localhost:{port}/events/ws") as ws:
                    msg = await asyncio.wait_for(ws.receive(), timeout=10)
                    data = json.loads(msg.data)
                    assert data["event"] == "heartbeat"
        finally:
            await runner.cleanup()


# ---------------------------------------------------------------------------
# SSE tests
# ---------------------------------------------------------------------------

class TestSSE:
    async def test_sse_connect_and_receive(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                # Push a message
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "SSE Test", "body": "Hello via SSE"},
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(
                    f"http://localhost:{port}/events",
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    assert resp.status == 200
                    assert "text/event-stream" in resp.headers.get("Content-Type", "")

                    buffer = b""
                    try:
                        while True:
                            chunk = await asyncio.wait_for(resp.content.read(1024), timeout=10)
                            if not chunk:
                                break
                            buffer += chunk
                            if b"\n\n" in buffer:
                                break
                    except asyncio.TimeoutError:
                        pass

                    text = buffer.decode()
                    assert "event:" in text
        finally:
            await runner.cleanup()


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------

class TestPersistence:
    async def test_messages_survive_restart(self, bridge_app, auth_headers):
        app, port = bridge_app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()

        try:
            async with ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/push",
                    json={"title": "Persistent", "body": "Survive restart"},
                    headers=auth_headers,
                ) as _:
                    pass

                async with session.get(f"http://localhost:{port}/messages") as resp:
                    data = await resp.json()
                    assert len(data) == 1
                    msg_id = data[0]["id"]
        finally:
            await runner.cleanup()

        # Create a new app instance (simulating restart) with same DB
        import importlib
        import bridge.config
        importlib.reload(bridge.config)

        app2 = create_app()
        port2 = unused_port()
        runner2 = web.AppRunner(app2)
        await runner2.setup()
        site2 = web.TCPSite(runner2, "localhost", port2)
        await site2.start()

        try:
            async with ClientSession() as session:
                async with session.get(f"http://localhost:{port2}/messages/{msg_id}") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["title"] == "Persistent"
        finally:
            await runner2.cleanup()
