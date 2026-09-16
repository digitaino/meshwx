"""The debug bridge's datagram feed.

Every v5 datagram the bot puts on the air passes through
`MeshcoreRadio.send_channel_data`, and that one call site publishes the
exact bytes here — after `Scheduler.transmit` stamped the seq, and again
for the echo resend, which repeats those same bytes. A bridge client
(the iOS app in the simulator, over an SSH tunnel) subscribes and sees
the air traffic without a radio of its own.

No FastAPI here on purpose: `radio.py` must not depend on the portal
extra. The HTTP surface lives in `portal/routes/bridge.py`.

The ring is a debug aid, not a log: it holds the last `RING` datagrams in
memory, the same public bytes that were just flooded on `#meshwx`, and is
never written to disk.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

RING = 200          # datagrams kept for a client that connects late
QUEUE = 200         # per-subscriber backlog before the slowest frames are dropped


@dataclass(frozen=True)
class FeedDatagram:
    """One transmitted datagram as the feed reports it."""

    cursor: int      # strictly increasing, 1-based; the `since` client cursor
    ts: float        # unix seconds, when the radio took the frame
    data_type: int   # the MeshCore data-type namespace (0xFF10 for MeshWX v5)
    hex: str         # the datagram bytes, lower-case hex
    length: int
    resend: bool     # the echo resend of a datagram already in the feed
    attempt: int     # 0 for the first transmission, 1.. for each resend

    def to_dict(self) -> dict:
        return {
            "cursor": self.cursor,
            "ts": self.ts,
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts)),
            "data_type": self.data_type,
            "hex": self.hex,
            "length": self.length,
            "resend": self.resend,
            "attempt": self.attempt,
        }


class DatagramFeed:
    """Singleton ring of transmitted datagrams with live subscribers."""

    def __init__(self, maxlen: int = RING):
        self._ring: deque[FeedDatagram] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue] = set()
        self._cursor = 0

    @property
    def cursor(self) -> int:
        """The cursor of the newest datagram (0 when nothing has gone out)."""
        return self._cursor

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, data: bytes, data_type: int, *, resend: bool = False,
                attempt: int = 0, ts: float | None = None) -> FeedDatagram:
        """Record one datagram that just went on the air and fan it out."""
        self._cursor += 1
        item = FeedDatagram(
            cursor=self._cursor,
            ts=time.time() if ts is None else ts,
            data_type=int(data_type),
            hex=bytes(data).hex(),
            length=len(data),
            resend=resend,
            attempt=attempt,
        )
        self._ring.append(item)
        for q in list(self._subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass          # a client that cannot keep up misses frames, never blocks the radio
        return item

    def since(self, cursor: int | None = None, limit: int = RING) -> list[FeedDatagram]:
        """The datagrams the ring still holds after `cursor`, oldest first.

        `None` (no cursor) means "everything the ring has": a client that has
        never connected gets the catch-up, one that has gets only what it
        missed. A cursor older than the ring silently starts at the oldest
        datagram still held — `gap` in the reply says so.
        """
        items = list(self._ring)
        if cursor is not None:
            items = [i for i in items if i.cursor > cursor]
        return items[-limit:] if limit and len(items) > limit else items

    def gap_after(self, cursor: int | None) -> bool:
        """True when datagrams after `cursor` have already left the ring."""
        if cursor is None or not self._ring:
            return False
        return self._ring[0].cursor > cursor + 1

    async def subscribe(self, cursor: int | None = None):
        """Yield the catch-up from the ring, then every new datagram.

        The queue is registered before the catch-up is taken, so a datagram
        sent in between is queued rather than lost; its cursor is then
        already in the catch-up, and the dedupe below drops the copy.
        """
        q: asyncio.Queue[FeedDatagram] = asyncio.Queue(maxsize=QUEUE)
        self._subscribers.add(q)
        try:
            last = 0
            for item in self.since(cursor):
                last = item.cursor
                yield item
            while True:
                item = await q.get()
                if item.cursor <= last:
                    continue
                last = item.cursor
                yield item
        finally:
            self._subscribers.discard(q)

    def reset(self) -> None:
        """Drop the ring and the cursor (tests only)."""
        self._ring.clear()
        self._cursor = 0


datagram_feed = DatagramFeed()
