"""Server-Sent Events with a heartbeat.

Every live stream in the portal (logs, text-bot traffic, broadcast log) goes
through here: a hello frame so the browser knows the stream is up, one JSON
frame per item, and a comment frame every `ping_s` seconds so idle proxies
and the browser's EventSource keep the connection open.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from starlette.responses import StreamingResponse

HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def sse_response(source: AsyncIterator, ping_s: float = 20.0) -> StreamingResponse:
    """Stream `source` (an async iterator of JSON-serialisable items) as SSE."""

    async def gen():
        q: asyncio.Queue = asyncio.Queue()

        async def pump():
            async for item in source:
                await q.put(item)

        pump_task = asyncio.create_task(pump())
        getter = asyncio.ensure_future(q.get())
        try:
            yield 'data: {"hello": true}\n\n'
            while True:
                # asyncio.wait (not wait_for) so a timeout never cancels the
                # pending get(): no item can be lost between two pings.
                done, _ = await asyncio.wait({getter}, timeout=ping_s)
                if not done:
                    yield ": ping\n\n"
                    continue
                yield "data: " + json.dumps(getter.result()) + "\n\n"
                getter = asyncio.ensure_future(q.get())
        finally:
            getter.cancel()
            pump_task.cancel()
            await asyncio.wait({pump_task}, timeout=1.0)   # let the source's finally run

    return StreamingResponse(gen(), media_type="text/event-stream", headers=HEADERS)
