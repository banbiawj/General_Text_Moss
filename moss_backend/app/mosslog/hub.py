from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .serializer import safe_serialize


class EventHub:
    def __init__(self, max_events: int = 1000):
        self.max_events = max_events
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._next_id = 1
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    @property
    def subscribers(self) -> set[asyncio.Queue]:
        return set(self._subscribers)

    def publish(self, tag: str, message: Any = None, **fields: Any) -> dict[str, Any]:
        with self._lock:
            event = {
                "id": self._next_id,
                "time": datetime.now(timezone.utc).astimezone().isoformat(),
                "tag": str(tag),
                "message": safe_serialize(message),
                "fields": safe_serialize(fields),
            }
            self._next_id += 1
            self._events.append(event)
            subscribers = dict(self._subscribers)

        self._broadcast(event, subscribers)
        return event

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._events.clear()
            clear_event = {"type": "clear"}
            subscribers = dict(self._subscribers)

        self._broadcast(clear_event, subscribers)
        return clear_event

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[queue] = loop
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    def _broadcast(self, event: dict[str, Any], subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop]) -> None:
        for queue, loop in subscribers.items():
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass
