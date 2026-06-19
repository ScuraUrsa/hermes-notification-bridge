"""WebSocket and SSE client connection manager."""

import asyncio
import json
import logging
import time
import uuid
from typing import Callable

from aiohttp import web, WSMsgType

from .models import Message

log = logging.getLogger("bridge.ws_manager")


class ClientManager:
    """Manages WebSocket and SSE client connections."""

    def __init__(self):
        self._ws_clients: dict[str, web.WebSocketResponse] = {}
        self._sse_responses: dict[str, web.StreamResponse] = {}
        self._on_broadcast: list[Callable] = []

    def register_ws(self, ws: web.WebSocketResponse) -> str:
        client_id = str(uuid.uuid4())
        self._ws_clients[client_id] = ws
        log.info("WebSocket client connected: %s (total: %d)", client_id, len(self._ws_clients))
        return client_id

    def unregister_ws(self, client_id: str):
        self._ws_clients.pop(client_id, None)
        log.info("WebSocket client disconnected: %s (total: %d)", client_id, len(self._ws_clients))

    def register_sse(self, response: web.StreamResponse) -> str:
        client_id = str(uuid.uuid4())
        self._sse_responses[client_id] = response
        log.info("SSE client connected: %s (total: %d)", client_id, len(self._sse_responses))
        return client_id

    def unregister_sse(self, client_id: str):
        self._sse_responses.pop(client_id, None)
        log.info("SSE client disconnected: %s (total: %d)", client_id, len(self._sse_responses))

    @property
    def ws_count(self) -> int:
        return len(self._ws_clients)

    @property
    def sse_count(self) -> int:
        return len(self._sse_responses)

    @property
    def total_count(self) -> int:
        return self.ws_count + self.sse_count

    async def broadcast(self, msg: Message):
        """Send a message to all connected clients."""
        event = msg.to_push_event()
        payload = json.dumps(event)
        sse_payload = f"event: message\ndata: {json.dumps(event['data'])}\n\n"

        # WebSocket clients
        disconnected = []
        for cid, ws in self._ws_clients.items():
            try:
                await ws.send_str(payload)
            except (ConnectionResetError, ConnectionAbortedError):
                disconnected.append(cid)
        for cid in disconnected:
            self.unregister_ws(cid)

        # SSE clients
        sse_disconnected = []
        for cid, resp in self._sse_responses.items():
            try:
                await resp.write(sse_payload.encode())
            except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
                sse_disconnected.append(cid)
        for cid in sse_disconnected:
            self.unregister_sse(cid)

        log.info("Broadcast message %s to %d clients", msg.id, self.total_count)

    async def send_heartbeat(self):
        """Send heartbeat to all connected clients."""
        heartbeat = json.dumps({"event": "heartbeat", "data": {"time": int(time.time())}})
        sse_heartbeat = f"event: heartbeat\ndata: {json.dumps({'time': int(time.time())})}\n\n"

        disconnected = []
        for cid, ws in self._ws_clients.items():
            try:
                await ws.send_str(heartbeat)
            except (ConnectionResetError, ConnectionAbortedError):
                disconnected.append(cid)
        for cid in disconnected:
            self.unregister_ws(cid)

        sse_disconnected = []
        for cid, resp in self._sse_responses.items():
            try:
                await resp.write(sse_heartbeat.encode())
            except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
                sse_disconnected.append(cid)
        for cid in sse_disconnected:
            self.unregister_sse(cid)


# Global singleton
client_manager = ClientManager()
