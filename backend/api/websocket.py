"""
WebSocket Manager
Handles real-time broadcasting of download progress, AI status,
and library updates to all connected browser clients.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Maintains a set of active WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.debug("WS client connected. Total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.debug("WS client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send a JSON event to all connected clients. Silently drops dead connections."""
        if not self._connections:
            return
        message = json.dumps(event, default=str)
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Application-level singleton
ws_manager = WebSocketManager()
