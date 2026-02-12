from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ChatSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, chat_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[chat_id].add(websocket)

    async def disconnect(self, chat_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(chat_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(chat_id, None)

    async def send_to_chat(self, chat_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            sockets = list(self._connections.get(chat_id, set()))
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                await self.disconnect(chat_id, websocket)


chat_socket_manager = ChatSocketManager()
