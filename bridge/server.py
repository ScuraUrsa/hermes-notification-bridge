"""Notification Bridge — main aiohttp server.

Hermes Agent pushes messages here. Conduit/OWUI subscribes via WebSocket or SSE.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from aiohttp import web, WSMsgType

from .config import config
from .models import Message, MessageStore, ClientStore
from .ws_manager import client_manager

log = logging.getLogger("bridge.server")

# Global stores
msg_store: Optional[MessageStore] = None
client_store: Optional[ClientStore] = None


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — health check."""
    return web.json_response({
        "status": "ok",
        "service": "hermes-notification-bridge",
        "clients": client_manager.total_count,
        "uptime": int(time.time() - request.app["start_time"]),
    })


async def handle_push(request: web.Request) -> web.Response:
    """POST /push — Hermes pushes a message here."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Validate auth
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {config.auth_key}"
    if auth != expected:
        return web.json_response({"error": "Unauthorized"}, status=401)

    # Validate required fields
    title = data.get("title", "")
    body = data.get("body", "")
    if not body:
        return web.json_response({"error": "body is required"}, status=400)

    # Create and store message
    msg = Message.create(
        source=data.get("source", "system"),
        title=title,
        body=body,
        priority=data.get("priority", "normal"),
        type=data.get("type", "notification"),
        action=data.get("action"),
        voice=data.get("voice", False),
        tts_text=data.get("tts_text"),
        metadata=data.get("metadata"),
    )
    msg_store.insert(msg)

    # Broadcast to all connected clients
    asyncio.ensure_future(client_manager.broadcast(msg))

    # Cleanup old messages
    msg_store.cleanup_old(config.max_messages)

    return web.json_response({"status": "delivered", "id": msg.id}, status=201)


async def handle_messages(request: web.Request) -> web.Response:
    """GET /messages — get recent messages.

    Query params:
      - limit (int, default 50)
      - offset (int, default 0)
      - voice (bool, default false) — filter voice-only messages
      - since (int, timestamp) — messages after this timestamp
    """
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))
    voice_only = request.query.get("voice", "").lower() in ("true", "1")
    since = request.query.get("since")

    if since:
        since_ts = int(since)
        all_msgs = msg_store.get_recent(limit=config.max_messages)
        filtered = [m for m in all_msgs if m.created_at > since_ts]
        if voice_only:
            filtered = [m for m in filtered if m.voice]
        return web.json_response([m.to_dict() for m in filtered[:limit]])

    msgs = msg_store.get_recent(limit=limit, offset=offset, voice_only=voice_only)
    return web.json_response([m.to_dict() for m in msgs])


async def handle_message_get(request: web.Request) -> web.Response:
    """GET /messages/{id} — get a single message."""
    msg_id = request.match_info.get("id")
    msg = msg_store.get(msg_id)
    if not msg:
        return web.json_response({"error": "Message not found"}, status=404)
    return web.json_response(msg.to_dict())


async def handle_message_read(request: web.Request) -> web.Response:
    """POST /messages/{id}/read — mark message as read."""
    msg_id = request.match_info.get("id")
    if msg_store.mark_read(msg_id):
        return web.json_response({"status": "marked_read", "id": msg_id})
    return web.json_response({"error": "Message not found or already read"}, status=404)


async def handle_stats(request: web.Request) -> web.Response:
    """GET /stats — bridge statistics."""
    stats = msg_store.get_stats()
    stats["connected_clients"] = client_manager.total_count
    stats["ws_clients"] = client_manager.ws_count
    stats["sse_clients"] = client_manager.sse_count
    return web.json_response(stats)


async def handle_test_last_delivery(request: web.Request) -> web.Response:
    """GET /test/last-delivery — test endpoint for E2E tests."""
    msgs = msg_store.get_recent(limit=1)
    if not msgs:
        return web.json_response({"status": "no_messages"})
    msg = msgs[0]
    return web.json_response({
        "status": "ok",
        "message": msg.to_dict(),
        "delivered_to": msg.delivered_to,
    })


async def handle_events_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /events/ws — WebSocket endpoint for Conduit."""
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)

    client_id = client_manager.register_ws(ws)
    client_store.register(client_id, client_type="conduit", user_agent=request.headers.get("User-Agent", ""))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                # Client can send pong or commands
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "pong":
                        client_store.register(client_id, client_type="conduit")
                except json.JSONDecodeError:
                    pass
            elif msg.type == WSMsgType.ERROR:
                log.error("WebSocket error: %s", ws.exception())
    finally:
        client_manager.unregister_ws(client_id)

    return ws


async def handle_events_sse(request: web.Request) -> web.StreamResponse:
    """GET /events — SSE endpoint for browser/OWUI."""
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    client_id = client_manager.register_sse(resp)
    client_store.register(client_id, client_type="owui-browser", user_agent=request.headers.get("User-Agent", ""))

    try:
        # Keep connection open — heartbeats keep it alive
        while True:
            await asyncio.sleep(config.heartbeat_interval)
            try:
                await resp.write(f"event: heartbeat\ndata: {json.dumps({'time': int(time.time())})}\n\n".encode())
            except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
                break
    finally:
        client_manager.unregister_sse(client_id)

    return resp


async def heartbeat_loop(app: web.Application):
    """Periodic heartbeat broadcast to all clients."""
    while True:
        await asyncio.sleep(config.heartbeat_interval)
        await client_manager.send_heartbeat()


async def on_startup(app: web.Application):
    """Initialize stores and start background tasks."""
    global msg_store, client_store
    msg_store = MessageStore()
    client_store = ClientStore()
    app["start_time"] = int(time.time())
    app["heartbeat_task"] = asyncio.create_task(heartbeat_loop(app))
    log.info("Notification Bridge started on %s:%d", config.host, config.port)


async def on_shutdown(app: web.Application):
    """Cleanup on shutdown."""
    if "heartbeat_task" in app:
        app["heartbeat_task"].cancel()
    log.info("Notification Bridge shutting down")


def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Routes
    app.router.add_get("/health", handle_health)
    app.router.add_post("/push", handle_push)
    app.router.add_get("/messages", handle_messages)
    app.router.add_get("/messages/{id}", handle_message_get)
    app.router.add_post("/messages/{id}/read", handle_message_read)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/test/last-delivery", handle_test_last_delivery)
    app.router.add_get("/events/ws", handle_events_ws)
    app.router.add_get("/events", handle_events_sse)

    return app


def main():
    """Run the bridge server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = create_app()
    web.run_app(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
