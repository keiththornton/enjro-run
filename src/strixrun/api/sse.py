"""Server-sent events from the in-process bus."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from strixrun.events import EventBus

KEEPALIVE_S = 15.0


def sse_response(request: Request, bus: EventBus, topics: set[str],
                 transform: Callable[[Any], Any] = lambda d: d) -> StreamingResponse:
    sub = bus.subscribe(topics)

    async def gen():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(sub.get(), KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                data = json.dumps(transform(ev.data), default=str)
                yield f"event: {ev.topic}\ndata: {data}\n\n"
        finally:
            sub.close()

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
