"""In-process pub/sub.

Topics: request.completed, model.state, model.log, download.progress,
binary.progress, settings.changed.

Two ways to listen:
- `on(topic, fn)`: a synchronous handler, used by RequestLog and Metrics. A failing
  handler is logged and never breaks the publisher.
- `subscribe(topics)`: a bounded queue for SSE feeds. A slow consumer loses its
  oldest events instead of blocking the publisher.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    topic: str
    data: Any
    ts: float = field(default_factory=time.time)


class Subscription:
    def __init__(self, bus: "EventBus", topics: set[str], size: int) -> None:
        self._bus = bus
        self.topics = topics
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=size)
        self.dropped = 0

    def _offer(self, ev: Event) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(ev)

    async def get(self) -> Event:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    def close(self) -> None:
        self._bus._subs.discard(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class EventBus:
    def __init__(self, queue_size: int = 1000) -> None:
        self._queue_size = queue_size
        self._handlers: dict[str, list[Callable[[Event], None]]] = {}
        self._subs: set[Subscription] = set()

    def on(self, topic: str, fn: Callable[[Event], None]) -> None:
        self._handlers.setdefault(topic, []).append(fn)

    def subscribe(self, topics: set[str]) -> Subscription:
        sub = Subscription(self, set(topics), self._queue_size)
        self._subs.add(sub)
        return sub

    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, topic: str, data: Any) -> Event:
        ev = Event(topic, data)
        for fn in self._handlers.get(topic, []):
            try:
                fn(ev)
            except Exception:
                log.exception("event handler failed for %s", topic)
        for sub in list(self._subs):
            if topic in sub.topics:
                sub._offer(ev)
        return ev
