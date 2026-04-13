"""In-memory activity log for the portal.

Provides a bounded circular buffer of recent events (requests received,
responses sent, scheduled broadcasts) that the portal can display.
Also tracks rolling aggregate stats (message counts, bytes) by time window.

Supports real-time streaming via SSE: call `subscribe()` to get an async
generator that yields new events as they arrive.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class EventDir(str, Enum):
    IN = "in"
    OUT = "out"


@dataclass
class Event:
    ts: float
    direction: EventDir
    event_type: str  # e.g. "v2_request", "v2_response", "v1_refresh", "broadcast", "send_fail"
    summary: str     # one-line human description
    detail: dict = field(default_factory=dict)  # structured metadata

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts)),
            "direction": self.direction.value,
            "event_type": self.event_type,
            "summary": self.summary,
            "detail": self.detail,
        }


class ActivityLog:
    """Singleton in-memory event log with rolling stats.

    Supports real-time streaming: SSE subscribers register via
    `subscribe()` and receive new events as they arrive.
    """

    def __init__(self, maxlen: int = 500):
        self._events: deque[Event] = deque(maxlen=maxlen)
        # Rolling counters: list of (timestamp, msg_count, byte_count) tuples
        self._sends: deque[tuple[float, int, int]] = deque(maxlen=5000)
        # SSE subscribers — set of asyncio.Queue instances that receive new events
        self._subscribers: set[asyncio.Queue] = set()

    def record(
        self,
        direction: EventDir,
        event_type: str,
        summary: str,
        detail: dict | None = None,
    ) -> None:
        event = Event(
            ts=time.time(),
            direction=direction,
            event_type=event_type,
            summary=summary,
            detail=detail or {},
        )
        self._events.append(event)
        # Notify all SSE subscribers
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow subscriber — drop the event rather than block

    def record_send(self, msg_count: int, byte_count: int) -> None:
        """Record outgoing message stats for aggregate tracking."""
        self._sends.append((time.time(), msg_count, byte_count))

    def recent(self, limit: int = 100) -> list[dict]:
        """Return the most recent events, newest first."""
        events = list(self._events)
        events.reverse()
        return [e.to_dict() for e in events[:limit]]

    def stats(self, window_minutes: int = 60) -> dict:
        """Aggregate send stats for the given time window."""
        cutoff = time.time() - (window_minutes * 60)
        total_msgs = 0
        total_bytes = 0
        for ts, msgs, nbytes in self._sends:
            if ts >= cutoff:
                total_msgs += msgs
                total_bytes += nbytes
        return {
            "window_minutes": window_minutes,
            "messages": total_msgs,
            "bytes": total_bytes,
        }

    async def subscribe(self):
        """Async generator that yields new Event objects as they arrive.

        Used by the SSE endpoint to push real-time updates to the portal.
        The generator runs indefinitely until the client disconnects.
        """
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self._subscribers.discard(q)


# Module-level singleton
activity_log = ActivityLog()
