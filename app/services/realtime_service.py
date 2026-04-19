"""Server-Sent Events (SSE) hub for real-time platform notifications.

Clients connect via GET /platform/events and receive a stream of JSON events.
Any backend service can broadcast events through the global hub.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any


class _SSEHub:
    """In-process pub/sub for SSE connections."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._queues.discard(q)

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        payload = json.dumps({
            "type": event_type,
            "data": data or {},
            "ts": time.time(),
        })
        dead: list[asyncio.Queue[str]] = []
        for q in self._queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._queues.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._queues)


hub = _SSEHub()


def broadcast_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Convenience function for broadcasting from sync code."""
    hub.broadcast(event_type, data)
